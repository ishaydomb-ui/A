import { useI18n } from '../i18n.ts';
import { useTheme } from '../lib/theme.ts';

/**
 * Names the mode you would switch TO, the same convention as LanguageToggle:
 * one button, no menu semantics, and the label itself says what happens.
 */
export function ThemeToggle() {
  const { t } = useI18n();
  const { theme, setTheme } = useTheme();
  const next = theme === 'dark' ? 'light' : 'dark';

  return (
    <button
      type="button"
      className="btn btn-sm btn-secondary"
      onClick={() => setTheme(next)}
      aria-label={`${t.theme}: ${next === 'dark' ? t.switchToDark : t.switchToLight}`}
    >
      {next === 'dark' ? t.switchToDark : t.switchToLight}
    </button>
  );
}
