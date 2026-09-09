/**
 * The clinical field registry: the single definition of what a medication
 * record contains, how it is labelled in both languages, how it groups in the
 * detail view, and whether it is meaningful in a side-by-side comparison.
 *
 * Taxonomy is deliberately split into four independent fields — therapeutic
 * group, drug class, drug family and mechanism — because the source website
 * conflated them (see the workbook's "Mixed hierarchy" finding).
 */
export const FIELD_GROUPS = [
  'identity',
  'taxonomy',
  'administration',
  'dosing',
  'pharmacokinetics',
  'indications',
  'safety',
  'monitoring',
  'notes',
] as const;
export type FieldGroup = (typeof FIELD_GROUPS)[number];

export const FIELD_GROUP_LABELS: Record<FieldGroup, { en: string; he: string }> = {
  identity: { en: 'Identity', he: 'זיהוי' },
  taxonomy: { en: 'Classification', he: 'סיווג' },
  administration: { en: 'Administration', he: 'צורת מתן' },
  dosing: { en: 'Dosing', he: 'מינון' },
  pharmacokinetics: { en: 'Pharmacokinetics', he: 'פרמקוקינטיקה' },
  indications: { en: 'Indications', he: 'התוויות' },
  safety: { en: 'Safety', he: 'בטיחות' },
  monitoring: { en: 'Monitoring', he: 'ניטור' },
  notes: { en: 'Clinical notes', he: 'הערות קליניות' },
};

export interface FieldDef {
  key: string;
  group: FieldGroup;
  label: { en: string; he: string };
  /** Included in the side-by-side comparison view. */
  comparable: boolean;
  /** Contributes to the free-text search index. */
  searchable: boolean;
  /** Rendered as a comma-separated list of chips rather than a paragraph. */
  list?: boolean;
  /** Long prose — rendered as a block, never in a comparison column. */
  prose?: boolean;
}

export const FIELDS: readonly FieldDef[] = [
  { key: 'generic_name', group: 'identity', label: { en: 'Generic name', he: 'שם גנרי' }, comparable: true, searchable: true },
  { key: 'trade_names', group: 'identity', label: { en: 'Trade names', he: 'שמות מסחריים' }, comparable: true, searchable: true, list: true },
  { key: 'therapeutic_group', group: 'taxonomy', label: { en: 'Therapeutic group', he: 'קבוצה טיפולית' }, comparable: true, searchable: true },
  { key: 'drug_class', group: 'taxonomy', label: { en: 'Drug class', he: 'מחלקת תרופות' }, comparable: true, searchable: true },
  { key: 'drug_family', group: 'taxonomy', label: { en: 'Drug family', he: 'משפחת תרופות' }, comparable: true, searchable: true },
  { key: 'mechanism', group: 'taxonomy', label: { en: 'Mechanism of action', he: 'מנגנון פעולה' }, comparable: true, searchable: true },
  { key: 'starting_age', group: 'administration', label: { en: 'Starting age', he: 'גיל התחלה' }, comparable: true, searchable: false },
  { key: 'formulation', group: 'administration', label: { en: 'Formulation / route', he: 'צורת מתן' }, comparable: true, searchable: true },
  { key: 'available_strengths', group: 'administration', label: { en: 'Available strengths', he: 'חוזקים זמינים' }, comparable: true, searchable: false },
  { key: 'dose_range', group: 'dosing', label: { en: 'Dose range', he: 'טווח מינון' }, comparable: true, searchable: false },
  { key: 'starting_dose', group: 'dosing', label: { en: 'Starting dose', he: 'מינון התחלתי' }, comparable: true, searchable: false },
  { key: 'titration', group: 'dosing', label: { en: 'Titration', he: 'טיטרציה' }, comparable: true, searchable: false },
  { key: 'maximum_dose', group: 'dosing', label: { en: 'Maximum dose', he: 'מינון מרבי' }, comparable: true, searchable: false },
  { key: 'onset', group: 'pharmacokinetics', label: { en: 'Onset of effect', he: 'תחילת השפעה' }, comparable: true, searchable: false },
  { key: 'duration', group: 'pharmacokinetics', label: { en: 'Duration of effect', he: 'משך השפעה' }, comparable: true, searchable: false },
  { key: 'adult_indications', group: 'indications', label: { en: 'Adult indications', he: 'התוויות למבוגרים' }, comparable: false, searchable: true, prose: true },
  { key: 'pediatric_indications', group: 'indications', label: { en: 'Paediatric indications', he: 'התוויות לילדים' }, comparable: false, searchable: true, prose: true },
  { key: 'qtc_adults', group: 'safety', label: { en: 'QTc — adults', he: 'QTc — מבוגרים' }, comparable: true, searchable: false },
  { key: 'qtc_pediatrics', group: 'safety', label: { en: 'QTc — paediatrics', he: 'QTc — ילדים' }, comparable: true, searchable: false },
  { key: 'side_effects', group: 'safety', label: { en: 'Side effects', he: 'תופעות לוואי' }, comparable: false, searchable: true, list: true },
  { key: 'contraindications', group: 'safety', label: { en: 'Contraindications', he: 'התוויות נגד' }, comparable: false, searchable: true, list: true },
  { key: 'monitoring_tests', group: 'monitoring', label: { en: 'Monitoring tests', he: 'בדיקות ניטור' }, comparable: true, searchable: true, list: true },
  { key: 'clinical_notes', group: 'notes', label: { en: 'Clinical notes', he: 'הערות קליניות' }, comparable: false, searchable: true, prose: true },
];

export const FIELD_KEYS = FIELDS.map((f) => f.key);
export type FieldKey = (typeof FIELDS)[number]['key'];

const BY_KEY = new Map(FIELDS.map((f) => [f.key, f]));
export function getField(key: string): FieldDef | undefined {
  return BY_KEY.get(key);
}

export function isFieldKey(key: string): boolean {
  return BY_KEY.has(key);
}

export const COMPARABLE_FIELDS = FIELDS.filter((f) => f.comparable);
export const SEARCHABLE_FIELDS = FIELDS.filter((f) => f.searchable);

export function fieldsInGroup(group: FieldGroup): FieldDef[] {
  return FIELDS.filter((f) => f.group === group);
}

/** Maximum number of medications that may be compared at once. */
export const MAX_COMPARE = 3;
