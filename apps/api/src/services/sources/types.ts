/**
 * External source lookup.
 *
 * These providers SUGGEST where a clinical claim might be documented. They
 * never attach a citation, never fill in a clinical field, and never decide an
 * approval status: a person reads the suggestion, checks it, and accepts it.
 *
 * That restraint is the whole point. A citation the system invented would make
 * an unchecked claim look sourced, which is worse than having no citation at
 * all, because a missing one is visible.
 */
export interface SourceSuggestion {
  /** Which provider produced this. */
  provider: string;
  /** Stable identifier at the provider, so the claim can be re-checked. */
  externalId: string;
  title: string;
  url: string;
  /** Free-text describing what the document is, shown to the reviewer. */
  description: string | null;
  /** Publication or revision date at the source, as the provider states it. */
  publishedAt: string | null;
  /**
   * The jurisdiction the document actually governs — not the one the
   * catalogue needs. A US label says nothing about Israeli approval status,
   * and the interface says so rather than letting the two be conflated.
   */
  jurisdiction: string;
}

export interface SourceProvider {
  readonly name: string;
  readonly label: string;
  /** What this provider is good for, shown in the interface. */
  readonly describes: string;
  readonly jurisdiction: string;
  /**
   * Looks up documents for a medication. Returns an empty list rather than
   * throwing when nothing matches; throws only on a genuine failure.
   */
  search(genericName: string, options: { signal: AbortSignal; limit: number }): Promise<SourceSuggestion[]>;
}

export class SourceLookupError extends Error {
  constructor(
    readonly provider: string,
    message: string,
    readonly cause?: unknown,
  ) {
    super(message);
    this.name = 'SourceLookupError';
  }
}
