import { useCallback, useMemo, useState, type ReactNode } from 'react';
import { MAX_COMPARE } from '@med/shared';
import { CompareSelectionContext } from '../lib/compareSelection.ts';

export function CompareSelectionProvider({ children }: { children: ReactNode }) {
  const [selected, setSelected] = useState<string[]>([]);

  const toggle = useCallback((slug: string) => {
    setSelected((current) =>
      current.includes(slug)
        ? current.filter((s) => s !== slug)
        : current.length >= MAX_COMPARE
          ? current
          : [...current, slug],
    );
  }, []);

  const remove = useCallback((slug: string) => {
    setSelected((current) => current.filter((s) => s !== slug));
  }, []);

  const clear = useCallback(() => setSelected([]), []);

  const value = useMemo(() => ({ selected, toggle, remove, clear }), [selected, toggle, remove, clear]);

  return <CompareSelectionContext.Provider value={value}>{children}</CompareSelectionContext.Provider>;
}
