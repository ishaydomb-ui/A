import { createContext, useContext } from 'react';

export type Theme = 'light' | 'dark';

export interface ThemeValue {
  theme: Theme;
  setTheme(theme: Theme): void;
}

export const ThemeContext = createContext<ThemeValue | null>(null);

export function useTheme(): ThemeValue {
  const value = useContext(ThemeContext);
  if (!value) throw new Error('useTheme must be used inside the theme provider');
  return value;
}

const STORAGE_KEY = 'medcat.theme';

/**
 * Light unless someone has explicitly switched to dark. Deliberately not
 * driven by the device's own colour-scheme setting — a phone or laptop left
 * in system dark mode should not silently darken a shared clinical screen
 * nobody chose to darken.
 */
export function readStoredTheme(): Theme {
  try {
    if (localStorage.getItem(STORAGE_KEY) === 'dark') return 'dark';
  } catch {
    // Storage can be unavailable (private mode); fall through to light.
  }
  return 'light';
}

export function storeTheme(theme: Theme): void {
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // A stored preference is a convenience, not a requirement.
  }
}
