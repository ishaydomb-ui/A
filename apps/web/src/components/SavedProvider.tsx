import { useCallback, useMemo, useState, type ReactNode } from 'react';
import { SavedContext, readSavedEntries, writeSavedEntries, type SavedEntry } from '../lib/saved.ts';

export function SavedProvider({ children }: { children: ReactNode }) {
  const [entries, setEntries] = useState<SavedEntry[]>(() => readSavedEntries());

  const isSaved = useCallback((slug: string) => entries.some((e) => e.slug === slug), [entries]);

  const toggle = useCallback((entry: Omit<SavedEntry, 'savedAt'>) => {
    setEntries((current) => {
      const next = current.some((e) => e.slug === entry.slug)
        ? current.filter((e) => e.slug !== entry.slug)
        : [{ ...entry, savedAt: Date.now() }, ...current];
      writeSavedEntries(next);
      return next;
    });
  }, []);

  const remove = useCallback((slug: string) => {
    setEntries((current) => {
      const next = current.filter((e) => e.slug !== slug);
      writeSavedEntries(next);
      return next;
    });
  }, []);

  const value = useMemo(() => ({ entries, isSaved, toggle, remove }), [entries, isSaved, toggle, remove]);

  return <SavedContext.Provider value={value}>{children}</SavedContext.Provider>;
}
