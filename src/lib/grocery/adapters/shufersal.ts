import type {
  StoreAdapter,
  CartLineRequest,
  CartLineResult,
  FillCartOptions,
  FillCartResult,
} from "./types";
import { ChallengeError } from "./types";

/**
 * Shufersal basket automation.
 *
 * Shufersal has no public ordering API, but it does run a conventional web
 * storefront (login -> search -> add to cart), which is why this is the chain
 * we automate first. Everything here stops at a filled basket.
 *
 * Operational notes that matter in practice:
 *  - Shufersal moved online fulfilment to robotic warehouses; baskets for a
 *    given delivery lock the evening before. Schedule runs well ahead of the slot.
 *  - If a CAPTCHA or SMS challenge appears we abort and hand back to a human.
 *    We do not attempt to solve or bypass it.
 *
 * Selectors are the fragile part of any storefront automation and are kept in
 * one block so a site redesign is a small, obvious fix.
 */

const SEL = {
  loginUrl: "https://www.shufersal.co.il/online/he/login",
  searchUrl: (q: string) =>
    `https://www.shufersal.co.il/online/he/search?text=${encodeURIComponent(q)}`,
  cartUrl: "https://www.shufersal.co.il/online/he/cart",
  username: "#j_username",
  password: "#j_password",
  submit: "button[type=submit]",
  loggedIn: "[data-test=user-menu], .userMenu, #userMenu",
  challenge: "iframe[src*=recaptcha], .captcha, #captcha",
  productTile: ".SEARCH-PRODUCT-ITEM, .miglog-prod",
  productName: ".text, .miglog-prod-name",
  productPrice: ".number, .miglog-price",
  addButton: "button.js-add-to-cart, .btnAddToCart",
};

export const shufersalAdapter: StoreAdapter = {
  key: "shufersal",
  label: "שופרסל",
  maturity: "supported",

  async fillCart(lines: CartLineRequest[], opts: FillCartOptions): Promise<FillCartResult> {
    // Imported lazily so the Next.js server bundle never pulls in Playwright.
    const { chromium } = await import("playwright");

    const warnings: string[] = [];
    const results: CartLineResult[] = [];

    const browser = await chromium.launch({
      headless: opts.headless ?? true,
      executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH || undefined,
    });
    const context = await browser.newContext({
      locale: "he-IL",
      timezoneId: "Asia/Jerusalem",
      ...(opts.session ? { storageState: JSON.parse(opts.session) } : {}),
    });
    const page = await context.newPage();

    try {
      // Reuse the stored session where possible; log in only when it has lapsed.
      await page.goto(SEL.cartUrl, { waitUntil: "domcontentloaded", timeout: 45_000 });
      const alreadyIn = await page.locator(SEL.loggedIn).first().isVisible().catch(() => false);

      if (!alreadyIn) {
        await page.goto(SEL.loginUrl, { waitUntil: "domcontentloaded", timeout: 45_000 });

        if (await page.locator(SEL.challenge).first().isVisible().catch(() => false)) {
          throw new ChallengeError(
            "Shufersal presented a CAPTCHA at login. Stopping - a human needs to sign in " +
              "once in a visible browser so a fresh session can be stored.",
          );
        }

        await page.fill(SEL.username, opts.username);
        await page.fill(SEL.password, opts.password);
        await page.click(SEL.submit);
        await page.waitForLoadState("networkidle", { timeout: 45_000 }).catch(() => {});

        if (await page.locator(SEL.challenge).first().isVisible().catch(() => false)) {
          throw new ChallengeError("Shufersal challenged the login (CAPTCHA/OTP). Stopping.");
        }
        const ok = await page.locator(SEL.loggedIn).first().isVisible().catch(() => false);
        if (!ok) throw new Error("Login did not complete - credentials may be stale.");

        opts.onSession?.(JSON.stringify(await context.storageState()));
      }

      for (const line of lines) {
        const query = line.itemCode || line.name;
        try {
          await page.goto(SEL.searchUrl(query), {
            waitUntil: "domcontentloaded",
            timeout: 45_000,
          });
          const tiles = page.locator(SEL.productTile);
          const count = await tiles.count();

          if (count === 0) {
            results.push({ requested: line.name, qty: line.qty, status: "not_found" });
            continue;
          }

          const first = tiles.first();
          const matchedName =
            (await first.locator(SEL.productName).first().textContent().catch(() => null))?.trim() ??
            undefined;
          const priceText = await first
            .locator(SEL.productPrice)
            .first()
            .textContent()
            .catch(() => null);
          const price = priceText ? Number(priceText.replace(/[^\d.]/g, "")) : undefined;

          const addBtn = first.locator(SEL.addButton).first();
          if (!(await addBtn.isVisible().catch(() => false))) {
            results.push({
              requested: line.name,
              qty: line.qty,
              status: "failed",
              matchedName,
              note: "no add-to-cart control on the tile",
            });
            continue;
          }

          // Clicking N times is what the storefront expects for quantity.
          for (let i = 0; i < Math.max(1, Math.round(line.qty)); i++) {
            await addBtn.click();
            await page.waitForTimeout(400);
          }

          results.push({
            requested: line.name,
            qty: line.qty,
            status: "added",
            matchedName,
            price,
          });
        } catch (err) {
          results.push({
            requested: line.name,
            qty: line.qty,
            status: "failed",
            note: (err as Error).message,
          });
        }
      }

      // Land on the cart so the screenshot shows exactly what is waiting.
      await page.goto(SEL.cartUrl, { waitUntil: "domcontentloaded" }).catch(() => {});
      let screenshotPath: string | undefined;
      if (opts.screenshotDir) {
        screenshotPath = `${opts.screenshotDir}/shufersal-${Date.now()}.png`;
        await page.screenshot({ path: screenshotPath, fullPage: true }).catch(() => {});
      }

      const added = results.filter((r) => r.status === "added");
      return {
        chain: "shufersal",
        lines: results,
        addedCount: added.length,
        missedCount: results.length - added.length,
        estimatedTotal: added.reduce((s, r) => s + (r.price ?? 0) * r.qty, 0),
        cartUrl: SEL.cartUrl,
        screenshotPath,
        warnings,
      };
    } finally {
      await browser.close().catch(() => {});
    }
  },
};
