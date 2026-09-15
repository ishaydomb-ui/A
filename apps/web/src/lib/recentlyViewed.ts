export interface RecentlyViewedEntry {
  slug: string;
  genericName: string;
  tradeNames: string | null;
  viewedAt: number;
}

const STORAGE_KEY = 'medcat.recentlyViewed';
const MAX_ENTRIES = 8;

export function readRecentlyViewed(): RecentlyViewedEntry[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (e): e is RecentlyViewedEntry =>
        e && typeof e === 'object' && typeof (e as RecentlyViewedEntry).slug === 'string',
    );
  } catch {
    return [];
  }
}

/** Moves this medication to the front, dropping any older duplicate. */
export function pushRecentlyViewed(entry: Omit<RecentlyViewedEntry, 'viewedAt'>): void {
  try {
    const next = [
      { ...entry, viewedAt: Date.now() },
      ...readRecentlyViewed().filter((e) => e.slug !== entry.slug),
    ].slice(0, MAX_ENTRIES);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    // A recency list is a convenience, not a requirement.
  }
}
