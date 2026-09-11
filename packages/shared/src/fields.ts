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
  { key: 'brand_names_israel', group: 'identity', label: { en: 'Brand names in Israel', he: 'שמות מסחריים בישראל' }, comparable: true, searchable: true, list: true },
  { key: 'formulations_israel', group: 'identity', label: { en: 'Available formulations in Israel', he: 'פורמולות הקיימות בישראל' }, comparable: false, searchable: true, prose: true },
  { key: 'starting_age', group: 'administration', label: { en: 'Starting age', he: 'גיל התחלה' }, comparable: true, searchable: false },
  { key: 'formulation', group: 'administration', label: { en: 'Formulation / route', he: 'צורת מתן' }, comparable: true, searchable: true },
  { key: 'available_strengths', group: 'administration', label: { en: 'Available strengths', he: 'חוזקים זמינים' }, comparable: true, searchable: false },
  { key: 'dose_range', group: 'dosing', label: { en: 'Dose range', he: 'טווח מינון' }, comparable: true, searchable: false },
  { key: 'starting_dose', group: 'dosing', label: { en: 'Starting dose', he: 'מינון התחלתי' }, comparable: true, searchable: false },
  { key: 'starting_dose_adults', group: 'dosing', label: { en: 'Starting dose — adults', he: 'מינון התחלתי — מבוגרים' }, comparable: true, searchable: false },
  { key: 'starting_dose_pediatrics', group: 'dosing', label: { en: 'Starting dose — children and adolescents', he: 'מינון התחלתי — ילדים ומתבגרים' }, comparable: true, searchable: false },
  { key: 'titration', group: 'dosing', label: { en: 'Titration', he: 'טיטרציה' }, comparable: true, searchable: false },
  { key: 'titration_adults', group: 'dosing', label: { en: 'Titration — adults', he: 'טיטרציה — מבוגרים' }, comparable: true, searchable: false },
  { key: 'titration_pediatrics', group: 'dosing', label: { en: 'Titration — children and adolescents', he: 'טיטרציה — ילדים ומתבגרים' }, comparable: true, searchable: false },
  { key: 'maximum_dose', group: 'dosing', label: { en: 'Maximum dose', he: 'מינון מרבי' }, comparable: true, searchable: false },
  { key: 'onset', group: 'pharmacokinetics', label: { en: 'Onset of effect', he: 'תחילת השפעה' }, comparable: true, searchable: false },
  { key: 'duration', group: 'pharmacokinetics', label: { en: 'Duration of effect', he: 'משך השפעה' }, comparable: true, searchable: false },
  { key: 'adult_indications', group: 'indications', label: { en: 'Adult indications', he: 'התוויות למבוגרים' }, comparable: false, searchable: true, prose: true },
  { key: 'pediatric_indications', group: 'indications', label: { en: 'Paediatric indications', he: 'התוויות לילדים' }, comparable: false, searchable: true, prose: true },
  { key: 'qtc_adults', group: 'safety', label: { en: 'QTc — adults', he: 'QTc — מבוגרים' }, comparable: true, searchable: false },
  { key: 'qtc_pediatrics', group: 'safety', label: { en: 'QTc — paediatrics', he: 'QTc — ילדים' }, comparable: true, searchable: false },
  { key: 'side_effects', group: 'safety', label: { en: 'Side effects', he: 'תופעות לוואי' }, comparable: false, searchable: true, list: true },
  // The antipsychotic and antidepressant sheets grade side effects in a matrix
  // (WG, AKA, PKN… scored +/++/+++). The abbreviations are unreadable without
  // the source's own legend, so it travels with the record.
  { key: 'side_effect_legend', group: 'safety', label: { en: 'Side-effect abbreviations', he: 'מקרא קיצורים — תופעות לוואי' }, comparable: false, searchable: false, prose: true },
  { key: 'contraindications', group: 'safety', label: { en: 'Contraindications', he: 'התוויות נגד' }, comparable: false, searchable: true, list: true },
  { key: 'monitoring_tests', group: 'monitoring', label: { en: 'Monitoring tests', he: 'בדיקות ניטור' }, comparable: true, searchable: true, list: true },
  { key: 'clinical_notes', group: 'notes', label: { en: 'Clinical notes', he: 'הערות קליניות' }, comparable: false, searchable: true, prose: true },
  { key: 'comments', group: 'notes', label: { en: 'Comments', he: 'הערות' }, comparable: false, searchable: true, prose: true },
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

// ---------------------------------------------------------------------------
// Per-class presentation profiles
//
// The registry above is a superset: it holds every field any source sheet can
// fill. But the four sheets in the authoritative workbook are genuinely
// different tables, and showing one universal schema over all of them was
// wrong in both directions — it invented headings the source never had
// (grouping "starting age" under "Administration"), and it rendered fields a
// sheet has no column for as "not supplied", which reads as missing data
// rather than not-applicable. A reviewer reading "QTc — adults: not supplied"
// on an ADHD drug cannot tell that the ADHD table has no QTc column at all.
//
// A profile mirrors one sheet: its fields, in its column order, under its own
// headings. Anything the sheet has no column for is simply absent. Labels are
// overridden only where the sheet words a heading differently from the
// registry's neutral default, so the page reads like the table it came from.
//
// A record whose class has no profile falls back to the grouped view.
// ---------------------------------------------------------------------------

export const DRUG_CLASSES = ['Antipsychotics', 'Antidepressants', 'Mood stabilizers', 'ADHD'] as const;
export type DrugClassKey = (typeof DRUG_CLASSES)[number];

export interface ProfileField {
  key: string;
  /** Shown instead of the registry label, to match the source sheet's heading. */
  label?: { en: string; he: string };
}

export const CLASS_PROFILES: Record<DrugClassKey, readonly ProfileField[]> = {
  Antipsychotics: [
    { key: 'generic_name' },
    { key: 'drug_class' },
    { key: 'brand_names_israel' },
    { key: 'mechanism' },
    { key: 'starting_age' },
    { key: 'adult_indications' },
    { key: 'pediatric_indications' },
    { key: 'dose_range' },
    { key: 'titration_adults' },
    { key: 'titration_pediatrics' },
    { key: 'side_effects' },
    { key: 'side_effect_legend' },
    { key: 'qtc_adults', label: { en: 'QTc prolongation data — adults', he: 'נתוני הארכת QTc — מבוגרים' } },
    { key: 'qtc_pediatrics', label: { en: 'QTc prolongation data — paediatrics', he: 'נתוני הארכת QTc — ילדים' } },
    { key: 'comments' },
  ],
  Antidepressants: [
    { key: 'generic_name' },
    { key: 'drug_class' },
    // The antidepressant sheet words this column "שמות מסחריים בישראל".
    { key: 'brand_names_israel', label: { en: 'Trade names in Israel', he: 'שמות מסחריים בישראל' } },
    { key: 'formulations_israel' },
    { key: 'mechanism' },
    { key: 'starting_age' },
    { key: 'adult_indications' },
    { key: 'pediatric_indications' },
    { key: 'dose_range', label: { en: 'Dosage', he: 'מינונים' } },
    { key: 'starting_dose_adults' },
    { key: 'starting_dose_pediatrics' },
    { key: 'titration_adults' },
    { key: 'titration_pediatrics' },
    { key: 'side_effects' },
    { key: 'side_effect_legend' },
    { key: 'qtc_adults', label: { en: 'QTc prolongation data — adults', he: 'נתוני הארכת QTc — מבוגרים' } },
    { key: 'qtc_pediatrics', label: { en: 'QTc prolongation data — paediatrics', he: 'נתוני הארכת QTc — ילדים' } },
    { key: 'comments' },
  ],
  'Mood stabilizers': [
    { key: 'generic_name' },
    { key: 'drug_class', label: { en: 'Group', he: 'קבוצה' } },
    { key: 'brand_names_israel' },
    { key: 'mechanism' },
    { key: 'adult_indications' },
    { key: 'pediatric_indications' },
    { key: 'dose_range', label: { en: 'Dosage', he: 'מינון' } },
    { key: 'titration_pediatrics', label: { en: 'Max dose and titration', he: 'מינון מרבי וטיטרציה' } },
    { key: 'titration_adults', label: { en: 'Max dose and titration — adults', he: 'מינון מרבי וטיטרציה — מבוגרים' } },
    { key: 'side_effects' },
    { key: 'monitoring_tests', label: { en: 'Tests at baseline / during treatment', he: 'בדיקות בתחילת הטיפול ובמהלכו' } },
    { key: 'comments' },
  ],
  ADHD: [
    { key: 'generic_name' },
    { key: 'drug_class', label: { en: 'Group', he: 'קבוצה' } },
    { key: 'trade_names', label: { en: 'Trade name', he: 'שם מסחרי' } },
    { key: 'starting_age' },
    { key: 'mechanism' },
    { key: 'formulation', label: { en: 'Form', he: 'צורה' } },
    { key: 'available_strengths', label: { en: 'Dose', he: 'מינון' } },
    { key: 'dose_range' },
    { key: 'starting_dose' },
    { key: 'titration_adults', label: { en: 'Titration for adults', he: 'טיטרציה למבוגרים' } },
    { key: 'titration_pediatrics', label: { en: 'Titration for children / adolescents', he: 'טיטרציה לילדים ומתבגרים' } },
    { key: 'onset' },
    { key: 'duration' },
    { key: 'maximum_dose' },
    { key: 'side_effects' },
    { key: 'contraindications' },
    { key: 'comments' },
  ],
};

function isDrugClassKey(value: string): value is DrugClassKey {
  return (DRUG_CLASSES as readonly string[]).includes(value);
}

/**
 * The ordered fields to show for a record, given its therapeutic group.
 * Returns null when the group has no profile, so the caller keeps the
 * grouped fallback rather than showing an arbitrary subset.
 */
export function profileFor(therapeuticGroup: string | null | undefined): readonly ProfileField[] | null {
  if (!therapeuticGroup) return null;
  const key = therapeuticGroup.trim();
  return isDrugClassKey(key) ? CLASS_PROFILES[key] : null;
}

/** The heading to show for a field within a profile. */
export function profileLabel(entry: ProfileField, locale: 'en' | 'he'): string {
  if (entry.label) return entry.label[locale];
  return getField(entry.key)?.label[locale] ?? entry.key;
}
