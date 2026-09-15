import { config } from '../../config.js';
import { logger } from '../../lib/logger.js';
import { DailyMedProvider } from './dailymed.js';
import { SourceLookupError, type SourceProvider, type SourceSuggestion } from './types.js';

export * from './types.js';

const providers: SourceProvider[] = [new DailyMedProvider()];

export function listProviders(): Array<Pick<SourceProvider, 'name' | 'label' | 'describes' | 'jurisdiction'>> {
  return providers.map(({ name, label, describes, jurisdiction }) => ({
    name,
    label,
    describes,
    jurisdiction,
  }));
}

export interface LookupResult {
  suggestions: SourceSuggestion[];
  /** Providers that could not be reached, so the interface can say so. */
  unavailable: Array<{ provider: string; reason: string }>;
}

/**
 * A short-lived cache. These registries are a public good funded by someone
 * else; repeating the same query while a reviewer works through one record is
 * needless load on them and needless waiting for the reviewer.
 */
const cache = new Map<string, { at: number; result: LookupResult }>();
const CACHE_MS = 10 * 60_000;
const MAX_CACHE_ENTRIES = 500;

function cached(key: string): LookupResult | null {
  const hit = cache.get(key);
  if (!hit) return null;
  if (Date.now() - hit.at > CACHE_MS) {
    cache.delete(key);
    return null;
  }
  return hit.result;
}

function remember(key: string, result: LookupResult): void {
  if (cache.size >= MAX_CACHE_ENTRIES) {
    // Oldest insertion first, which Map iteration gives us for free.
    const oldest = cache.keys().next().value;
    if (oldest) cache.delete(oldest);
  }
  cache.set(key, { at: Date.now(), result });
}

/**
 * Asks every configured provider what documents exist for a medication.
 *
 * A provider that is unreachable is reported, not fatal: the reviewer sees
 * what was found and what could not be checked, and decides. Being offline
 * must never block the editorial work.
 */
export async function lookupSources(
  genericName: string,
  options: { limit?: number } = {},
): Promise<LookupResult> {
  const name = genericName.trim();
  if (!name) return { suggestions: [], unavailable: [] };
  if (!config.sources.enabled) {
    return {
      suggestions: [],
      unavailable: providers.map((p) => ({ provider: p.name, reason: 'Source lookup is switched off.' })),
    };
  }

  const limit = options.limit ?? 8;
  const key = `${name.toLowerCase()}|${limit}`;
  const hit = cached(key);
  if (hit) return hit;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), config.sources.timeoutMs);

  const outcomes = await Promise.allSettled(
    providers.map((provider) => provider.search(name, { signal: controller.signal, limit })),
  );
  clearTimeout(timer);

  const result: LookupResult = { suggestions: [], unavailable: [] };
  outcomes.forEach((outcome, i) => {
    const provider = providers[i];
    if (outcome.status === 'fulfilled') {
      result.suggestions.push(...outcome.value);
      return;
    }
    const reason =
      outcome.reason instanceof SourceLookupError
        ? outcome.reason.message
        : (outcome.reason as Error)?.name === 'AbortError'
          ? 'The lookup took too long.'
          : 'The lookup failed.';
    logger.warn({ provider: provider.name, err: outcome.reason }, 'source lookup failed');
    result.unavailable.push({ provider: provider.name, reason });
  });

  remember(key, result);
  return result;
}

/** Test seam: lets a suite install a provider without reaching the network. */
export function __setProvidersForTests(next: SourceProvider[]): () => void {
  const previous = [...providers];
  providers.length = 0;
  providers.push(...next);
  cache.clear();
  return () => {
    providers.length = 0;
    providers.push(...previous);
    cache.clear();
  };
}
