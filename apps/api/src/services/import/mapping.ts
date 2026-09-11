import { FIELDS, normalizeText } from '@med/shared';

/**
 * Header synonyms used to SUGGEST a column mapping. A suggestion is never
 * applied on its own: the importer must confirm or correct the mapping before
 * any row is staged, so a mis-titled column cannot silently land in the wrong
 * clinical field.
 */
const SYNONYMS: Record<string, string[]> = {
  generic_name: ['medication', 'generic name', 'generic', 'drug', 'drug name', 'name', 'שם גנרי', 'תרופה'],
  trade_names: ['trade names', 'trade name', 'brand', 'brand names', 'commercial names', 'שמות מסחריים', 'שם מסחרי'],
  brand_names_israel: ['brand names in israel', 'trade names in israel', 'brand names israel', 'שמות מסחריים בישראל'],
  formulations_israel: [
    'available formulations in israel',
    'formulations in israel',
    'פורמולות הקיימות בישראל',
    'פורמולות בישראל',
  ],
  therapeutic_group: ['therapeutic group', 'group', 'indication group', 'therapeutic area', 'קבוצה טיפולית'],
  drug_class: ['category class', 'category / class', 'class', 'category', 'drug class', 'מחלקה', 'מחלקת תרופות'],
  drug_family: ['drug family', 'family', 'משפחת תרופות'],
  mechanism: ['mechanism', 'mechanism of action', 'moa', 'מנגנון', 'מנגנון פעולה'],
  starting_age: ['starting age', 'start age', 'minimum age', 'age', 'גיל התחלה'],
  formulation: ['formulation', 'dosage form', 'form', 'route', 'צורת מתן', 'צורה'],
  available_strengths: ['available strengths', 'strengths', 'strength', 'חוזקים', 'חוזקים זמינים'],
  dose_range: ['dose range', 'dosage range', 'range', 'טווח מינון'],
  starting_dose: ['starting dose', 'initial dose', 'start dose', 'מינון התחלתי'],
  starting_dose_adults: ['starting dose adults', 'starting dose - adults', 'מינון התחלתי מבוגרים'],
  starting_dose_pediatrics: [
    'starting dose pediatrics',
    'starting dose children',
    'מינון התחלתי ילדים',
  ],
  titration: ['titration', 'titration schedule', 'טיטרציה'],
  // The workbook words these differently on every sheet ("Titration (Adults)",
  // "טיטרציית מינון - מבוגרים", "Titration for adults"), so all three map.
  titration_adults: [
    'titration adults',
    'titration (adults)',
    'titration for adults',
    'titration - adults',
    'max dose and titration adults',
    'טיטרציה מבוגרים',
    'טיטרציית מינון - מבוגרים',
  ],
  titration_pediatrics: [
    'titration pediatrics',
    'titration (pediatrics)',
    'titration for children',
    'titration for children/adolescents',
    'titration - pediatrics',
    'max dose and titration',
    'טיטרציה ילדים',
    'טטרציית מינון - ילדים ונוער',
    'טיטרציית מינון - ילדים ונוער',
  ],
  maximum_dose: ['maximum dose', 'max dose', 'maximal dose', 'מינון מרבי', 'מינון מקסימלי'],
  onset: ['onset', 'onset of action', 'onset of effect', 'תחילת השפעה'],
  duration: ['duration', 'duration of action', 'duration of effect', 'משך השפעה'],
  adult_indications: ['adult indications', 'adults indications', 'indications adults', 'התוויות למבוגרים'],
  pediatric_indications: ['pediatric indications', 'paediatric indications', 'children indications', 'התוויות לילדים'],
  qtc_adults: ['qtc adults', 'qtc adult', 'qtc mbugarim', 'qtc מבוגרים'],
  qtc_pediatrics: ['qtc pediatrics', 'qtc paediatrics', 'qtc children', 'qtc ילדים'],
  side_effects: ['side effects', 'adverse effects', 'adverse reactions', 'תופעות לוואי'],
  side_effect_legend: ['side effect abbreviations', 'side-effect legend', 'side effect legend', 'מקרא קיצורים'],
  contraindications: ['contraindications', 'contraindication', 'התוויות נגד'],
  monitoring_tests: [
    'monitoring tests',
    'monitoring',
    'labs',
    'lab tests',
    'tests at baseline/during treatment',
    'בדיקות ניטור',
  ],
  // "Comments" and "הערות" belong to the workbook's own Comments column, which
  // is its own field — they used to fall into clinical_notes, which is a
  // different thing (the app's editorial notes, not the source's).
  clinical_notes: ['clinical notes', 'remarks', 'הערות קליניות'],
  comments: ['comments', 'comment', 'הערות'],
};

/** Columns that are metadata rather than clinical content. */
const META_COLUMNS: Record<string, string> = {
  'source order': '__source_order',
  'source url': '__source_url',
  'extracted at': '__extracted_at',
  'validation status': '__validation_status',
};

const LOOKUP = new Map<string, string>();
for (const [fieldKey, synonyms] of Object.entries(SYNONYMS)) {
  for (const synonym of synonyms) LOOKUP.set(normalizeText(synonym), fieldKey);
}
for (const field of FIELDS) {
  LOOKUP.set(normalizeText(field.label.en), field.key);
  LOOKUP.set(normalizeText(field.label.he), field.key);
  LOOKUP.set(normalizeText(field.key.replace(/_/g, ' ')), field.key);
}

export interface MappingSuggestion {
  header: string;
  fieldKey: string | null;
  /** 'exact' when the header matched a known name, 'none' when unrecognised. */
  confidence: 'exact' | 'none';
  meta?: string;
}

export function suggestMapping(headers: string[]): MappingSuggestion[] {
  const used = new Set<string>();
  return headers.map((header) => {
    const normalized = normalizeText(header);
    const meta = META_COLUMNS[normalized];
    if (meta) return { header, fieldKey: null, confidence: 'none' as const, meta };

    const fieldKey = LOOKUP.get(normalized);
    // A field may only be fed by one column; a second match is left unmapped
    // for a human to resolve rather than overwriting the first.
    if (fieldKey && !used.has(fieldKey)) {
      used.add(fieldKey);
      return { header, fieldKey, confidence: 'exact' as const };
    }
    return { header, fieldKey: null, confidence: 'none' as const };
  });
}

/** Field keys that must be mapped before an import can proceed. */
export const REQUIRED_FIELDS = ['generic_name'];

export function validateMapping(mapping: Record<string, string | null>): string[] {
  const mapped = new Set(Object.values(mapping).filter(Boolean) as string[]);
  const problems: string[] = [];
  for (const required of REQUIRED_FIELDS) {
    if (!mapped.has(required)) problems.push(`Column for "${required}" is not mapped.`);
  }
  const counts = new Map<string, number>();
  for (const value of Object.values(mapping)) {
    if (!value) continue;
    counts.set(value, (counts.get(value) ?? 0) + 1);
  }
  for (const [fieldKey, count] of counts) {
    if (count > 1) problems.push(`Field "${fieldKey}" is mapped from ${count} columns.`);
  }
  const known = new Set(FIELDS.map((f) => f.key));
  for (const value of counts.keys()) {
    if (!known.has(value)) problems.push(`Unknown field "${value}" in mapping.`);
  }
  return problems;
}
