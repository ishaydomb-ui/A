import type {
  StoreAdapter,
  CartLineRequest,
  CartLineResult,
  FillCartOptions,
  FillCartResult,
} from "./types";

/**
 * Tiv Taam basket automation - deliberately not implemented yet.
 *
 * Why: Tiv Taam's ordering runs on SelfPoint's "טיב טעם בקליק" platform, which is
 * app-first. There is no established web automation path the way there is for
 * Shufersal, so a browser adapter here would be guesswork that breaks silently.
 *
 * What still works for Tiv Taam today, without this adapter:
 *  - full product and price data, from the public price-transparency feed
 *  - meal planning, list building and price comparison
 *  - an exported list to shop from manually or paste into the app
 *
 * To promote this to a real adapter, the honest options are, in order of
 * preference: check whether SelfPoint exposes a documented ordering API for
 * Tiv Taam, ask Tiv Taam directly for integration access, or (last resort)
 * build a web adapter mirroring shufersal.ts once the web flow is confirmed
 * stable. Until one of those is done this stays unsupported rather than flaky.
 */

export const tivtaamAdapter: StoreAdapter = {
  key: "tivtaam",
  label: "טיב טעם",
  maturity: "unsupported",

  async fillCart(lines: CartLineRequest[], _opts: FillCartOptions): Promise<FillCartResult> {
    const results: CartLineResult[] = lines.map((l) => ({
      requested: l.name,
      qty: l.qty,
      status: "failed",
      note: "Tiv Taam basket automation is not implemented",
    }));

    return {
      chain: "tivtaam",
      lines: results,
      addedCount: 0,
      missedCount: lines.length,
      estimatedTotal: 0,
      warnings: [
        "Tiv Taam ordering is app-first (SelfPoint) with no confirmed web automation path. " +
          "Prices and list building work; use the exported list in the app, or switch this " +
          "run to Shufersal.",
      ],
    };
  },
};
