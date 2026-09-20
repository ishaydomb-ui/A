/**
 * Every clinical field is stored as an explicit state plus optional text.
 *
 * A blank cell is NEVER interpreted as "not relevant". Importing a blank cell
 * records `not_supplied` — a factual statement about the source document, not
 * a clinical claim. Only a human reviewer may assert `not_applicable`, and
 * `unknown` means the value is genuinely undetermined.
 */
export const VALUE_STATES = ['provided', 'unknown', 'not_supplied', 'not_applicable'] as const;
export type ValueState = (typeof VALUE_STATES)[number];

export interface FieldValue {
  state: ValueState;
  /** Verbatim source text. Non-empty only when state === 'provided'. */
  text: string | null;
}

export const NOT_SUPPLIED: FieldValue = { state: 'not_supplied', text: null };

export function provided(text: string): FieldValue {
  return { state: 'provided', text };
}

/**
 * Converts a raw imported cell into a FieldValue without altering its content.
 * Whitespace at the edges is trimmed (a formatting artefact, not clinical
 * content); nothing else is normalised, corrected, expanded or inferred.
 */
export function fromImportedCell(raw: unknown): FieldValue {
  if (raw === null || raw === undefined) return { ...NOT_SUPPLIED };
  const text = String(raw).trim();
  if (text === '') return { ...NOT_SUPPLIED };
  return { state: 'provided', text };
}

export function isDisplayable(value: FieldValue | null | undefined): boolean {
  return !!value && value.state === 'provided' && !!value.text;
}

/** Bilingual labels for the non-provided states, for UI rendering. */
export const VALUE_STATE_LABELS: Record<ValueState, { en: string; he: string }> = {
  provided: { en: '', he: '' },
  unknown: { en: 'Unknown', he: 'לא ידוע' },
  not_supplied: { en: 'Not supplied', he: 'לא סופק' },
  not_applicable: { en: 'Not applicable', he: 'לא רלוונטי' },
};
