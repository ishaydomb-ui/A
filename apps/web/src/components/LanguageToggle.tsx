import { useI18n } from '../i18n.ts';

/**
 * A single toggle rather than a dropdown: with two languages, one button that
 * names the language you would switch TO is faster and needs no menu
 * semantics.
 */
export function LanguageToggle() {
  const { locale, setLocale, t } = useI18n();
  const next = locale === 'en' ? 'he' : 'en';

  return (
    <button
      type="button"
      className="btn btn-sm btn-secondary"
      onClick={() => setLocale(next)}
      lang={next}
      // The button's own text is in the target language, so the accessible
      // name spells out the action in the language currently in use.
      aria-label={`${t.language}: ${next === 'he' ? t.switchToHebrew : t.switchToEnglish}`}
    >
      {next === 'he' ? t.switchToHebrew : t.switchToEnglish}
    </button>
  );
}
