import { all, run } from "../db";

/**
 * Product catalogue and prices.
 *
 * Source: Israel's 2014 Food Act (price transparency) obliges every chain with
 * 3+ stores - Shufersal and Tiv Taam included - to publish full price and promo
 * files daily, publicly, no account required. That makes pricing and product
 * lookup a legitimate data feed rather than scraping, and it works identically
 * for both chains.
 *
 * We only ever READ this feed. Filling an actual basket is a separate, gated
 * action handled by the browser worker.
 */

export interface ChainConfig {
  key: string;
  label: string;
  /** Publisher portal for the mandated price files. */
  portal: string;
  /**
   * Whether we have a working basket-automation adapter for this chain.
   * See src/lib/grocery/adapters/ for the current state of each.
   */
  cartAutomation: "supported" | "experimental" | "unsupported";
  notes: string;
}

export const CHAINS: Record<string, ChainConfig> = {
  shufersal: {
    key: "shufersal",
    label: "שופרסל",
    portal: "https://prices.shufersal.co.il/",
    cartAutomation: "supported",
    notes:
      "Public web storefront with a conventional login/search/cart flow. Well-trodden " +
      "automation path. Note the robotic-fulfilment cutoff: baskets for next-day delivery " +
      "lock the evening before, so the worker should run well ahead of the slot.",
  },
  tivtaam: {
    key: "tivtaam",
    label: "טיב טעם",
    portal: "https://url.publishedprices.co.il/",
    cartAutomation: "experimental",
    notes:
      "Ordering is app-first (SelfPoint 'טיב טעם בקליק'); the web storefront is thinner and " +
      "there is no established automation path. Prices still come from the public feed, so " +
      "planning and pricing work fully - only basket-filling is unproven.",
  },
};

/** Search the cached catalogue. This is what `search_products` calls. */
export function searchProducts(query: string, chain?: string, limit = 10) {
  const params: unknown[] = [`%${query}%`];
  let sql = `SELECT chain, item_code, name, manufacturer, unit, qty, price, promo
             FROM store_products WHERE name LIKE ?`;
  if (chain) {
    sql += ` AND chain = ?`;
    params.push(chain);
  }
  return all(`${sql} ORDER BY price ASC LIMIT ?`, [...params, limit]);
}

/**
 * Resolve a free-text shopping-list line ("חלב 3%") to a catalogue product.
 * Deliberately conservative: if nothing matches well we return null and the
 * item stays on the list unpriced rather than being silently swapped for
 * something else.
 */
export function resolveProduct(
  name: string,
  chain: string,
): { item_code: string; name: string; price: number } | null {
  const exact = all<{ item_code: string; name: string; price: number }>(
    `SELECT item_code, name, price FROM store_products
     WHERE chain = ? AND name LIKE ? ORDER BY price ASC LIMIT 1`,
    [chain, `%${name}%`],
  );
  if (exact.length) return exact[0];

  // Fall back to matching on the most distinctive word (longest token).
  const token = name
    .split(/\s+/)
    .filter((t) => t.length > 2)
    .sort((a, b) => b.length - a.length)[0];
  if (!token) return null;

  const loose = all<{ item_code: string; name: string; price: number }>(
    `SELECT item_code, name, price FROM store_products
     WHERE chain = ? AND name LIKE ? ORDER BY price ASC LIMIT 1`,
    [chain, `%${token}%`],
  );
  return loose[0] ?? null;
}

export function upsertProducts(
  chain: string,
  storeId: string,
  products: Array<{
    itemCode: string;
    name: string;
    manufacturer?: string;
    unit?: string;
    qty?: string;
    price?: number;
    promo?: unknown;
  }>,
) {
  const stmt = `INSERT INTO store_products
      (chain, store_id, item_code, name, manufacturer, unit, qty, price, promo, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
     ON CONFLICT(chain, store_id, item_code) DO UPDATE SET
       name=excluded.name, price=excluded.price, promo=excluded.promo,
       updated_at=datetime('now')`;
  for (const p of products) {
    run(stmt, [
      chain,
      storeId,
      p.itemCode,
      p.name,
      p.manufacturer ?? null,
      p.unit ?? null,
      p.qty ?? null,
      p.price ?? null,
      p.promo ? JSON.stringify(p.promo) : null,
    ]);
  }
  return products.length;
}

export function catalogueStats() {
  return all<{ chain: string; n: number; updated: string }>(
    `SELECT chain, COUNT(*) AS n, MAX(updated_at) AS updated
     FROM store_products GROUP BY chain`,
  );
}
