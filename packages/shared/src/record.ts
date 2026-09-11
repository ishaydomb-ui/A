import type { FieldValue } from './values.js';
import { NOT_SUPPLIED } from './values.js';
import type { Locale } from './validation.js';
import type { WorkflowState } from './workflow.js';

/**
 * Clinical content is stored per locale. The Hebrew and English texts are
 * independent records of what a source said — one is never machine-translated
 * from the other, because that would be silent inference of medical content.
 */
export type LocalizedField = Record<Locale, FieldValue>;

export type MedicationData = Record<string, LocalizedField>;

export function emptyLocalized(): LocalizedField {
  return { en: { ...NOT_SUPPLIED }, he: { ...NOT_SUPPLIED } };
}

/**
 * Reads a field in the requested locale. If that locale has no content but
 * the other does, the other is returned together with a flag so the UI can
 * label it (e.g. "English only") rather than silently pretending it is a
 * translation.
 */
export interface ResolvedField {
  value: FieldValue;
  /** Locale the returned text actually came from. */
  locale: Locale;
  /** True when the requested locale had no content and we fell back. */
  fallback: boolean;
}

export function resolveField(
  data: MedicationData,
  key: string,
  locale: Locale,
): ResolvedField {
  const other: Locale = locale === 'en' ? 'he' : 'en';
  const field = data[key];
  if (!field) return { value: { ...NOT_SUPPLIED }, locale, fallback: false };
  const wanted = field[locale];
  if (wanted && wanted.state === 'provided' && wanted.text) {
    return { value: wanted, locale, fallback: false };
  }
  const alt = field[other];
  if (alt && alt.state === 'provided' && alt.text) {
    return { value: alt, locale: other, fallback: true };
  }
  // Neither locale has text: report the requested locale's declared state,
  // preferring an explicit assertion over a bare "not supplied".
  if (wanted && wanted.state !== 'not_supplied') return { value: wanted, locale, fallback: false };
  if (alt && alt.state !== 'not_supplied') return { value: alt, locale: other, fallback: true };
  return { value: wanted ?? { ...NOT_SUPPLIED }, locale, fallback: false };
}

export interface MedicationVersionSummary {
  id: string;
  medicationId: string;
  slug: string;
  versionNumber: number;
  state: WorkflowState;
  validationStatus: string;
  updatedAt: string;
}
