/**
 * Contract every supermarket adapter implements.
 *
 * Hard rule, enforced by the absence of any method for it: an adapter can log in,
 * search and fill a basket. It cannot check out, pay, or choose a delivery slot.
 * The run always ends with a basket a human opens and completes.
 */

export interface CartLineRequest {
  name: string;
  qty: number;
  /** Catalogue code resolved from the public price feed, when we have one. */
  itemCode?: string | null;
}

export interface CartLineResult {
  requested: string;
  qty: number;
  status: "added" | "not_found" | "ambiguous" | "failed";
  matchedName?: string;
  price?: number;
  note?: string;
}

export interface FillCartResult {
  chain: string;
  lines: CartLineResult[];
  addedCount: number;
  missedCount: number;
  estimatedTotal: number;
  /** Deep link the human opens to review and check out themselves. */
  cartUrl?: string;
  screenshotPath?: string;
  warnings: string[];
}

export interface StoreAdapter {
  key: string;
  label: string;
  /** How reliable basket automation currently is for this chain. */
  maturity: "supported" | "experimental" | "unsupported";
  /**
   * Fill the basket. Implementations must:
   *  - reuse a stored session and only log in when it has expired
   *  - stop and report if a CAPTCHA or 2FA challenge appears, never try to defeat it
   *  - never navigate to checkout/payment
   */
  fillCart(lines: CartLineRequest[], opts: FillCartOptions): Promise<FillCartResult>;
}

export interface FillCartOptions {
  username: string;
  password: string;
  /** Serialized cookies from a previous run, if any. */
  session?: string | null;
  headless?: boolean;
  /** Called so a refreshed session can be re-encrypted and stored. */
  onSession?: (session: string) => void;
  screenshotDir?: string;
}

export class ChallengeError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ChallengeError";
  }
}
