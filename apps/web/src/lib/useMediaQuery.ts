import { useEffect, useState } from 'react';

/**
 * Tracks a media query as React state, so a component can render a
 * genuinely different layout per breakpoint (not just hide/show the same
 * one) without duplicating interactive elements in the DOM at both sizes.
 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(
    () => typeof window !== 'undefined' && window.matchMedia(query).matches,
  );

  useEffect(() => {
    const mql = window.matchMedia(query);
    setMatches(mql.matches);
    const sync = (event: MediaQueryListEvent) => setMatches(event.matches);
    mql.addEventListener('change', sync);
    return () => mql.removeEventListener('change', sync);
  }, [query]);

  return matches;
}
