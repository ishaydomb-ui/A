import { normalizeText, type MedicationData, type Severity } from '@med/shared';

export interface DetectedFinding {
  severity: Severity;
  scope: string;
  fieldKey: string | null;
  issueType: string;
  evidence: string;
  recommendedAction: string;
  detector: string;
  rowNumber?: number;
}

export interface RowContext {
  rowNumber: number;
  /** Generic name as supplied, used as the scope label. */
  name: string;
  data: MedicationData;
  raw: Record<string, string | null>;
}

function textOf(data: MedicationData, key: string): string {
  const en = data[key]?.en;
  const he = data[key]?.he;
  if (en?.state === 'provided' && en.text) return en.text;
  if (he?.state === 'provided' && he.text) return he.text;
  return '';
}

/**
 * A detector inspects a staged row and reports anything a human should look
 * at. Detectors NEVER modify data — their only output is a finding.
 */
export type RowDetector = (ctx: RowContext) => DetectedFinding[];
export type BatchDetector = (rows: RowContext[]) => DetectedFinding[];

// --- Row-level detectors ----------------------------------------------------

/**
 * A generic-name cell holding what looks like a drug class abbreviation,
 * while the trade-names cell holds something that looks like a generic name.
 * This is the "NRI / Atomoxetine" inversion the source workbook flagged.
 */
const CLASS_ABBREVIATIONS = new Set([
  'nri', 'snri', 'ssri', 'ndri', 'maoi', 'tca', 'sari', 'nassa', 'ndris', 'sari',
  'stimulant', 'stimulants', 'non stimulant', 'non stimulants', 'alpha agonist',
]);

const suspectedLabelInversion: RowDetector = (ctx) => {
  const generic = textOf(ctx.data, 'generic_name').trim();
  const trade = textOf(ctx.data, 'trade_names').trim();
  if (!generic) return [];
  const normalized = normalizeText(generic);
  if (!CLASS_ABBREVIATIONS.has(normalized)) return [];
  return [{
    severity: 'high',
    scope: ctx.name,
    fieldKey: 'generic_name',
    issueType: 'Probable label inversion',
    evidence: `Generic name is "${generic}", which reads as a drug class` +
      (trade ? `, while trade names hold "${trade}".` : '.'),
    recommendedAction:
      'Confirm the generic name and trade names against the authoritative source. Do not auto-correct.',
    detector: 'suspected_label_inversion',
    rowNumber: ctx.rowNumber,
  }];
};

/**
 * A threshold phrase whose quantity is missing, such as
 * "above requires 29 gimel" where the dose threshold was dropped.
 *
 * Looking only for "a digit somewhere in the sentence" is not enough: the
 * broken sentence above still contains "29". What matters is whether the
 * threshold keyword itself is followed by a quantity, so the check walks the
 * words directly after the keyword, stepping over the small connective words
 * that legitimately sit between one and the other ("below the age of 6").
 */
const THRESHOLD_KEYWORDS = /\b(above|below|over|under|exceeds?|exceeding|greater than|less than)\b/gi;
const CONNECTIVES = new Set(['the', 'a', 'an', 'age', 'ages', 'of', 'dose', 'doses', 'weight']);
const LOOKAHEAD_WORDS = 4;

function thresholdHasQuantity(words: string[]): boolean {
  let steps = 0;
  for (const word of words) {
    if (steps >= LOOKAHEAD_WORDS) return false;
    if (/\d/.test(word)) return true;
    if (!CONNECTIVES.has(word.toLowerCase().replace(/[^a-z]/g, ''))) return false;
    steps++;
  }
  return false;
}

const incompleteThreshold: RowDetector = (ctx) => {
  const findings: DetectedFinding[] = [];
  for (const fieldKey of ['clinical_notes', 'maximum_dose', 'dose_range', 'starting_age']) {
    const text = textOf(ctx.data, fieldKey);
    if (!text) continue;

    let flagged: string | null = null;
    for (const sentence of text.split(/(?<=[.;])\s+/)) {
      THRESHOLD_KEYWORDS.lastIndex = 0;
      let match: RegExpExecArray | null;
      while ((match = THRESHOLD_KEYWORDS.exec(sentence)) !== null) {
        const after = sentence
          .slice(match.index + match[0].length)
          .trim()
          .split(/\s+/)
          .filter(Boolean);
        if (!thresholdHasQuantity(after)) {
          flagged = sentence.trim();
          break;
        }
      }
      if (flagged) break;
    }
    if (!flagged) continue;

    findings.push({
      severity: 'high',
      scope: ctx.name,
      fieldKey,
      issueType: 'Incomplete threshold',
      evidence: `Text states a threshold without a quantity: "${flagged}"`,
      recommendedAction: 'Recover the missing threshold from the authoritative source.',
      detector: 'incomplete_threshold',
      rowNumber: ctx.rowNumber,
    });
  }
  return findings;
};

/**
 * A bare physiological parameter listed as a side effect, with no direction.
 * "Blood pressure" alone does not say raised or lowered.
 */
const BARE_PARAMETERS = ['blood pressure', 'heart rate', 'weight', 'appetite', 'growth', 'pulse'];

const ambiguousSideEffect: RowDetector = (ctx) => {
  const text = textOf(ctx.data, 'side_effects');
  if (!text) return [];
  const items = text.split(/[,;\n]/).map((s) => s.trim()).filter(Boolean);
  const bare = items.filter((item) => BARE_PARAMETERS.includes(normalizeText(item)));
  if (bare.length === 0) return [];
  return [{
    severity: 'medium',
    scope: ctx.name,
    fieldKey: 'side_effects',
    issueType: 'Ambiguous wording',
    evidence: `Listed without a direction or explanation: ${bare.map((b) => `"${b}"`).join(', ')}.`,
    recommendedAction: 'Clarify from the source; preserve the original text until reviewed.',
    detector: 'ambiguous_side_effect',
    rowNumber: ctx.rowNumber,
  }];
};

/** Mixed Hebrew and Latin script inside one clinical value. */
const mixedScript: RowDetector = (ctx) => {
  const findings: DetectedFinding[] = [];
  for (const fieldKey of ['clinical_notes', 'contraindications', 'side_effects', 'adult_indications']) {
    const text = textOf(ctx.data, fieldKey);
    if (!text) continue;
    const hasHebrew = /[א-ת]/.test(text);
    const hasLatin = /[A-Za-z]{3,}/.test(text);
    if (hasHebrew && hasLatin) {
      findings.push({
        severity: 'medium',
        scope: ctx.name,
        fieldKey,
        issueType: 'Mixed-language content',
        evidence: `Value mixes Hebrew and Latin script: "${text.slice(0, 160)}${text.length > 160 ? '…' : ''}"`,
        recommendedAction:
          'Split the Hebrew and English content into their own locale fields, or confirm the mix is intended.',
        detector: 'mixed_script',
        rowNumber: ctx.rowNumber,
      });
    }
  }
  return findings;
};

/** Dose text whose units are formatted inconsistently. */
const unitFormatting: RowDetector = (ctx) => {
  const findings: DetectedFinding[] = [];
  for (const fieldKey of ['dose_range', 'starting_dose', 'maximum_dose', 'available_strengths']) {
    const text = textOf(ctx.data, fieldKey);
    if (!text) continue;
    const noSpace = /\d(mg|mcg|g|ml)\b/i.test(text);
    const withSpace = /\d\s+(mg|mcg|g|ml)\b/i.test(text);
    const dashes = /[–—]/.test(text);
    if (noSpace && withSpace) {
      findings.push({
        severity: 'low', scope: ctx.name, fieldKey,
        issueType: 'Unit formatting inconsistency',
        evidence: `Mixed spacing between number and unit: "${text}"`,
        recommendedAction: 'Normalise only in a derived display field, after approval. Keep the source text.',
        detector: 'unit_formatting', rowNumber: ctx.rowNumber,
      });
    } else if (dashes) {
      findings.push({
        severity: 'low', scope: ctx.name, fieldKey,
        issueType: 'Unit formatting inconsistency',
        evidence: `Range uses a typographic dash rather than a hyphen: "${text}"`,
        recommendedAction: 'Normalise only in a derived display field, after approval. Keep the source text.',
        detector: 'unit_formatting', rowNumber: ctx.rowNumber,
      });
    }
  }
  return findings;
};

/** A missing value in a field a physician would expect to be present. */
const EXPECTED_FIELDS = [
  'therapeutic_group', 'mechanism', 'formulation', 'dose_range',
  'starting_dose', 'maximum_dose', 'contraindications', 'side_effects',
];

const missingExpectedField: RowDetector = (ctx) => {
  const missing = EXPECTED_FIELDS.filter((key) => !textOf(ctx.data, key));
  if (missing.length === 0) return [];
  return [{
    severity: 'medium',
    scope: ctx.name,
    fieldKey: null,
    issueType: 'Missing values',
    evidence: `No value supplied for: ${missing.join(', ')}.`,
    recommendedAction:
      'Decide for each field whether it is unknown, not applicable, or simply not supplied by this source. ' +
      'A blank cell is recorded as "not supplied" and is not treated as "not applicable".',
    detector: 'missing_expected_field',
    rowNumber: ctx.rowNumber,
  }];
};

/** An uncited claim in a field where a source is mandatory before publishing. */
const CLAIM_FIELDS = ['qtc_adults', 'qtc_pediatrics', 'adult_indications', 'pediatric_indications'];

const uncitedClaim: RowDetector = (ctx) => {
  const present = CLAIM_FIELDS.filter((key) => textOf(ctx.data, key));
  if (present.length === 0) return [];
  return [{
    severity: 'high',
    scope: ctx.name,
    fieldKey: null,
    issueType: 'Claims lack citations',
    evidence: `Clinical claims imported with no source attached: ${present.join(', ')}.`,
    recommendedAction:
      'Attach a source, page, jurisdiction, approval status and review date to each claim before publishing.',
    detector: 'uncited_claim',
    rowNumber: ctx.rowNumber,
  }];
};

/** Indications recorded as free text with no approval status. */
const unstructuredIndication: RowDetector = (ctx) => {
  const adult = textOf(ctx.data, 'adult_indications');
  const paed = textOf(ctx.data, 'pediatric_indications');
  if (!adult && !paed) return [];
  const mentionsOffLabel = /off.?label/i.test(`${adult} ${paed}`);
  return [{
    severity: 'high',
    scope: ctx.name,
    fieldKey: 'adult_indications',
    issueType: 'Approval status not modeled',
    evidence: mentionsOffLabel
      ? 'Indication text mentions off-label use but carries no structured approval status or jurisdiction.'
      : 'Indications are free text with no structured approval status, age group or jurisdiction.',
    recommendedAction: 'Record approval status and jurisdiction per indication as a citation.',
    detector: 'unstructured_indication',
    rowNumber: ctx.rowNumber,
  }];
};

export const ROW_DETECTORS: RowDetector[] = [
  suspectedLabelInversion,
  incompleteThreshold,
  ambiguousSideEffect,
  mixedScript,
  unitFormatting,
  missingExpectedField,
  uncitedClaim,
  unstructuredIndication,
];

// --- Batch-level detectors --------------------------------------------------

/** Two rows describing the same medication. */
const duplicateRows: BatchDetector = (rows) => {
  const byName = new Map<string, RowContext[]>();
  for (const row of rows) {
    const key = normalizeText(textOf(row.data, 'generic_name'));
    if (!key) continue;
    const list = byName.get(key) ?? [];
    list.push(row);
    byName.set(key, list);
  }
  const findings: DetectedFinding[] = [];
  for (const [, group] of byName) {
    if (group.length < 2) continue;
    findings.push({
      severity: 'high',
      scope: group[0].name,
      fieldKey: 'generic_name',
      issueType: 'Duplicate rows',
      evidence: `The same generic name appears on rows ${group.map((g) => g.rowNumber).join(', ')}.`,
      recommendedAction:
        'Confirm whether these are distinct formulations that need distinct names, or a duplicated row.',
      detector: 'duplicate_rows',
      rowNumber: group[0].rowNumber,
    });
  }
  return findings;
};

/**
 * Identical long text repeated across several medications — usually
 * class-level boilerplate copied onto each drug.
 */
const repeatedClassText: BatchDetector = (rows) => {
  const findings: DetectedFinding[] = [];
  for (const fieldKey of ['side_effects', 'contraindications']) {
    const byText = new Map<string, RowContext[]>();
    for (const row of rows) {
      const text = textOf(row.data, fieldKey);
      if (text.length < 40) continue;
      const key = normalizeText(text);
      const list = byText.get(key) ?? [];
      list.push(row);
      byText.set(key, list);
    }
    for (const [, group] of byText) {
      if (group.length < 3) continue;
      findings.push({
        severity: 'medium',
        scope: `${group.length} medications`,
        fieldKey,
        issueType: 'Repeated class text',
        evidence:
          `Identical ${fieldKey.replace('_', ' ')} text on ${group.length} rows ` +
          `(${group.slice(0, 4).map((g) => g.name).join(', ')}${group.length > 4 ? ', …' : ''}).`,
        recommendedAction:
          'Confirm this is intentional class-level content rather than drug-specific detail that was copied.',
        detector: 'repeated_class_text',
        rowNumber: group[0].rowNumber,
      });
    }
  }
  return findings;
};

/**
 * The same label used as both a therapeutic group and a drug class, which is
 * the taxonomy mixing the source workbook flagged.
 */
const mixedTaxonomy: BatchDetector = (rows) => {
  const groups = new Set<string>();
  const classes = new Set<string>();
  for (const row of rows) {
    const g = normalizeText(textOf(row.data, 'therapeutic_group'));
    const c = normalizeText(textOf(row.data, 'drug_class'));
    if (g) groups.add(g);
    if (c) classes.add(c);
  }
  const overlap = [...groups].filter((g) => classes.has(g));
  if (overlap.length === 0) return [];
  return [{
    severity: 'medium',
    scope: 'multiple',
    fieldKey: 'therapeutic_group',
    issueType: 'Mixed hierarchy',
    evidence: `Used as both a therapeutic group and a drug class: ${overlap.join(', ')}.`,
    recommendedAction:
      'Keep therapeutic area, drug class, drug family and mechanism in separate fields.',
    detector: 'mixed_taxonomy',
  }];
};

/** A field left empty across most of the workbook. */
const highMissingness: BatchDetector = (rows) => {
  if (rows.length < 5) return [];
  const findings: DetectedFinding[] = [];
  for (const fieldKey of ['starting_age', 'monitoring_tests', 'pediatric_indications', 'qtc_adults']) {
    const missing = rows.filter((r) => !textOf(r.data, fieldKey)).length;
    const ratio = missing / rows.length;
    if (ratio < 0.5) continue;
    findings.push({
      severity: 'medium',
      scope: 'multiple',
      fieldKey,
      issueType: 'High missingness',
      evidence: `"${fieldKey}" is blank in ${missing} of ${rows.length} rows.`,
      recommendedAction:
        'Decide whether blank means unknown, not applicable, or not supplied, and record it explicitly.',
      detector: 'high_missingness',
    });
  }
  return findings;
};

export const BATCH_DETECTORS: BatchDetector[] = [
  duplicateRows,
  repeatedClassText,
  mixedTaxonomy,
  highMissingness,
];

export function runDetectors(rows: RowContext[]): DetectedFinding[] {
  const findings: DetectedFinding[] = [];
  for (const row of rows) {
    for (const detector of ROW_DETECTORS) findings.push(...detector(row));
  }
  for (const detector of BATCH_DETECTORS) findings.push(...detector(rows));
  return findings;
}
