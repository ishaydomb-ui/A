import { createContext, useContext } from 'react';

export interface CompareSelectionValue {
  selected: string[];
  toggle(slug: string): void;
  remove(slug: string): void;
  clear(): void;
}

/**
 * Lives at the app root, not inside the catalogue page, so a selection made
 * while browsing survives a trip to Explore or a medication's own page — the
 * bottom-nav Compare tab reads the same count from anywhere.
 */
export const CompareSelectionContext = createContext<CompareSelectionValue | null>(null);

export function useCompareSelection(): CompareSelectionValue {
  const value = useContext(CompareSelectionContext);
  if (!value) throw new Error('useCompareSelection must be used inside the compare-selection provider');
  return value;
}
