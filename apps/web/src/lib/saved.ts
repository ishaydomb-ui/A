import { createContext, useContext } from 'react';

export interface SavedEntry {
  slug: string;
  genericName: string;
  tradeNames: string | null;
  therapeuticGroup: string | null;
  savedAt: number;
}

export interface SavedValue {
  entries: SavedEntry[];
  isSaved(slug: string): boolean;
  toggle(entry: Omit<SavedEntry, 'savedAt'>): void;
  remove(slug: string): void;
}

export const SavedContext = createContext<SavedValue | null>(null);

export function useSaved(): SavedValue {
  const value = useContext(SavedContext);
  if (!value) throw new Error('useSaved must be used inside the saved-medications provider');
  return value;
}

const STORAGE_KEY = 'medcat.saved';

/**
 * A per-device bookmark list, not a clinical record: nothing here is
 * authoritative catalogue data, so it lives in localStorage rather than the
 * database and never needs a migration or a capability check.
 */
export function readSavedEntries(): SavedEntry[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (e): e is SavedEntry => e && typeof e === 'object' && typeof (e as SavedEntry).slug === 'string',
    );
  } catch {
    return [];
  }
}

export function writeSavedEntries(entries: SavedEntry[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(entries));
  } catch {
    // A saved list is a convenience, not a requirement.
  }
}
