/**
 * Pull the daily price files that Israeli chains are legally required to publish
 * (2014 Food Act, price transparency) and cache them locally.
 *
 * This is a public, no-account data source covering both Shufersal and Tiv Taam,
 * which is why product lookup and price comparison work for both chains even
 * though only Shufersal has basket automation.
 *
 * Run with:  npm run prices:sync -- --chain=shufersal
 *
 * NOTE: the portal endpoints below are the documented entry points, but each
 * chain's portal has its own listing format and some require a (public,
 * documented) login. This script has not been run against the live portals from
 * a sandbox with network egress to them - expect to adjust `discover()` per
 * chain the first time you run it for real.
 */
import { gunzipSync } from "node:zlib";
import { upsertProducts } from "../src/lib/grocery/prices";
import { db } from "../src/lib/db";

db();

interface Portal {
  chain: string;
  /** Page listing the day's gzipped XML files. */
  index: string;
  /** Some portals require a public login (username = chain name, blank password). */
  login?: { url: string; username: string; password: string };
}

const PORTALS: Record<string, Portal> = {
  shufersal: {
    chain: "shufersal",
    index: "https://prices.shufersal.co.il/FileObject/UpdateCategory?catID=2",
  },
  tivtaam: {
    chain: "tivtaam",
    index: "https://url.publishedprices.co.il/file/json/dir",
    login: {
      url: "https://url.publishedprices.co.il/login/user",
      username: "TivTaam",
      password: "",
    },
  },
};

const arg = process.argv.find((a) => a.startsWith("--chain="));
const chainKey = arg?.split("=")[1] ?? process.env.GROCERY_DEFAULT_CHAIN ?? "shufersal";
const portal = PORTALS[chainKey];
if (!portal) {
  console.error(`Unknown chain "${chainKey}". Known: ${Object.keys(PORTALS).join(", ")}`);
  process.exit(1);
}

/** Extract file URLs from the portal's index page. */
async function discover(p: Portal): Promise<string[]> {
  const res = await fetch(p.index);
  if (!res.ok) throw new Error(`index fetch failed: ${res.status}`);
  const body = await res.text();

  // Both portal styles ultimately expose links to .gz files; grab them generically.
  const urls = [...body.matchAll(/https?:\/\/[^"'\s<>]+?\.gz/gi)].map((m) => m[0]);
  // Price files, not promo or store files, are what we cache here.
  return [...new Set(urls)].filter((u) => /price/i.test(u)).slice(0, 5);
}

/**
 * The published XML uses slightly different tag casing per chain, so match
 * case-insensitively rather than assuming one schema.
 */
function parsePriceXml(xml: string) {
  const items: Parameters<typeof upsertProducts>[2] = [];
  const field = (block: string, name: string): string | undefined => {
    const m = block.match(new RegExp(`<${name}>([\\s\\S]*?)</${name}>`, "i"));
    return m?.[1]?.trim();
  };

  for (const match of xml.matchAll(/<Item>([\s\S]*?)<\/Item>/gi)) {
    const block = match[1];
    const itemCode = field(block, "ItemCode");
    const name = field(block, "ItemName") ?? field(block, "ItemNm");
    if (!itemCode || !name) continue;
    items.push({
      itemCode,
      name,
      manufacturer: field(block, "ManufacturerName"),
      unit: field(block, "UnitQty"),
      qty: field(block, "Quantity"),
      price: Number(field(block, "ItemPrice") ?? "") || undefined,
    });
  }
  return items;
}

async function main() {
  console.log(`Syncing ${chainKey} price catalogue…`);
  const files = await discover(portal);
  if (!files.length) {
    console.error(
      "No price files found. The portal's listing format has probably changed - " +
        "inspect the index page and adjust discover().",
    );
    process.exit(1);
  }

  let total = 0;
  for (const url of files) {
    const res = await fetch(url);
    if (!res.ok) {
      console.warn(`  skipped ${url} (${res.status})`);
      continue;
    }
    const buf = Buffer.from(await res.arrayBuffer());
    const xml = url.endsWith(".gz") ? gunzipSync(buf).toString("utf8") : buf.toString("utf8");

    const storeId = url.match(/-(\d{3,})-/)?.[1] ?? "default";
    const items = parsePriceXml(xml);
    total += upsertProducts(chainKey, storeId, items);
    console.log(`  ${items.length} products from store ${storeId}`);
  }

  console.log(`Done. ${total} products cached for ${chainKey}.`);
}

main().catch((err) => {
  console.error(err.message);
  process.exit(1);
});
