import { FIELD_KEYS, LOCALES, type Locale } from '@med/shared';
import type { FieldValue, MedicationData } from '@med/shared';

export interface FieldChange {
  fieldKey: string;
  locale: Locale;
  before: FieldValue | null;
  after: FieldValue | null;
  kind: 'added' | 'removed' | 'changed' | 'state_changed';
}

function same(a: FieldValue | undefined, b: FieldValue | undefined): boolean {
  const an = a ?? null;
  const bn = b ?? null;
  if (an === null && bn === null) return true;
  if (an === null || bn === null) return false;
  return an.state === bn.state && (an.text ?? null) === (bn.text ?? null);
}

function isEmpty(v: FieldValue | undefined): boolean {
  return !v || v.state !== 'provided' || !v.text;
}

/**
 * Field-level diff between two versions of a record. Used for the revision
 * history and for the change report shown before an import is committed, so a
 * reviewer can see exactly what a new workbook would alter.
 */
export function diffData(before: MedicationData, after: MedicationData): FieldChange[] {
  const changes: FieldChange[] = [];
  const keys = new Set<string>([...FIELD_KEYS, ...Object.keys(before), ...Object.keys(after)]);

  for (const fieldKey of keys) {
    for (const locale of LOCALES) {
      const b = before[fieldKey]?.[locale];
      const a = after[fieldKey]?.[locale];
      if (same(b, a)) continue;

      let kind: FieldChange['kind'];
      if (isEmpty(b) && !isEmpty(a)) kind = 'added';
      else if (!isEmpty(b) && isEmpty(a)) kind = 'removed';
      else if (isEmpty(b) && isEmpty(a)) kind = 'state_changed';
      else kind = 'changed';

      changes.push({ fieldKey, locale, before: b ?? null, after: a ?? null, kind });
    }
  }
  return changes;
}

export function hasChanges(before: MedicationData, after: MedicationData): boolean {
  return diffData(before, after).length > 0;
}
