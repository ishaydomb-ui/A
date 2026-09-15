import ExcelJS from 'exceljs';
import { FIELDS, VALUE_STATE_LABELS, type Locale } from '@med/shared';
import type { MedicationData } from '@med/shared';
import { query } from '../db/pool.js';

/**
 * Exports the catalogue as it currently stands: one row per medication, one
 * column per clinical field.
 *
 * Values are written exactly as stored. Where a field has no content the
 * export writes the state — Not supplied, Unknown, Not applicable — rather
 * than an empty cell, so a reader can tell "nobody recorded this" apart from
 * "this does not apply", which a blank cell cannot express.
 */
interface Row {
  slug: string;
  state: string;
  version_number: number;
  validation_status: string;
  published_unvalidated: boolean;
  reviewed_at: Date | null;
  published_at: Date | null;
  data: MedicationData;
  citation_count: number;
}

const HEADER_FILL: ExcelJS.Fill = {
  type: 'pattern',
  pattern: 'solid',
  fgColor: { argb: 'FF1F3B57' },
};

function valueFor(data: MedicationData, key: string, locale: Locale): string {
  const field = data[key];
  const own = field?.[locale];
  if (own?.state === 'provided' && own.text) return own.text;

  const other = field?.[locale === 'en' ? 'he' : 'en'];
  if (other?.state === 'provided' && other.text) return other.text;

  const state = own?.state ?? other?.state ?? 'not_supplied';
  // The state written out, never a blank cell.
  return VALUE_STATE_LABELS[state][locale] || VALUE_STATE_LABELS.not_supplied[locale];
}

export async function buildCatalogueWorkbook(
  options: { locale?: Locale; publishedOnly?: boolean } = {},
): Promise<ExcelJS.Workbook> {
  const locale = options.locale ?? 'en';
  const states = options.publishedOnly
    ? ['published']
    : ['published', 'approved', 'in_clinical_review', 'changes_requested', 'draft'];

  const { rows } = await query<Row>(
    `SELECT m.slug, v.state::text AS state, v.version_number, v.validation_status,
            v.published_unvalidated, v.reviewed_at, v.published_at, v.data,
            (SELECT count(*)::int FROM citations c WHERE c.version_id = v.id) AS citation_count
       FROM medication_versions v
       JOIN medications m ON m.id = v.medication_id
      WHERE v.state::text = ANY($1::text[])
      ORDER BY v.data #>> ARRAY['generic_name', 'en', 'text'], m.slug`,
    [states],
  );

  const workbook = new ExcelJS.Workbook();
  workbook.creator = 'Medication Catalogue';
  workbook.created = new Date();

  buildAboutSheet(workbook, rows, locale);
  buildCatalogueSheet(workbook, rows, locale);
  return workbook;
}

function styleHeader(sheet: ExcelJS.Worksheet): void {
  const header = sheet.getRow(1);
  header.font = { bold: true, color: { argb: 'FFFFFFFF' }, size: 11 };
  header.fill = HEADER_FILL;
  header.alignment = { vertical: 'middle', wrapText: true };
  header.height = 30;
  sheet.views = [{ state: 'frozen', ySplit: 1, xSplit: 1 }];
}

function buildAboutSheet(workbook: ExcelJS.Workbook, rows: Row[], locale: Locale): void {
  const sheet = workbook.addWorksheet('About');
  sheet.columns = [
    { header: 'Item', key: 'item', width: 34 },
    { header: 'Value', key: 'value', width: 96 },
  ];
  styleHeader(sheet);

  const unreviewed = rows.filter((r) => r.published_unvalidated).length;
  const uncited = rows.filter((r) => r.citation_count === 0).length;

  sheet.addRows([
    { item: 'Exported', value: new Date().toISOString().slice(0, 16).replace('T', ' ') + ' UTC' },
    { item: 'Language', value: locale === 'he' ? 'Hebrew, falling back to English' : 'English, falling back to Hebrew' },
    { item: 'Medications', value: rows.length },
    { item: 'Not clinically reviewed', value: unreviewed },
    { item: 'With no source attached', value: uncited },
  ]);

  sheet.addRow({});
  const warning = sheet.addRow({
    item: 'IMPORTANT',
    value:
      'This content has not been checked against an authoritative source and has not been ' +
      'reviewed by a clinician. It is a working copy for evaluation and correction. ' +
      'Do not use it for clinical decisions.',
  });
  warning.font = { bold: true, color: { argb: 'FF9C1F1F' } };
  warning.alignment = { wrapText: true, vertical: 'top' };
  warning.height = 44;

  sheet.addRow({});
  sheet.addRow({
    item: 'Empty fields',
    value:
      'No cell is left blank. "Not supplied" means the source document did not give a value; ' +
      '"Unknown" means it is undetermined; "Not applicable" means a reviewer decided it does ' +
      'not apply. A blank cell cannot tell these apart, which is why none are used.',
  }).alignment = { wrapText: true, vertical: 'top' };
}

function buildCatalogueSheet(workbook: ExcelJS.Workbook, rows: Row[], locale: Locale): void {
  const sheet = workbook.addWorksheet('Catalogue');

  sheet.columns = [
    { header: 'Medication', key: 'name', width: 26 },
    ...FIELDS.filter((f) => f.key !== 'generic_name').map((field) => ({
      header: field.label[locale],
      key: field.key,
      width: field.prose ? 52 : 26,
    })),
    { header: 'State', key: 'state', width: 12 },
    { header: 'Validation status', key: 'validation', width: 30 },
    { header: 'Sources attached', key: 'citations', width: 16 },
  ];
  styleHeader(sheet);

  for (const row of rows) {
    const record: Record<string, string | number> = {
      name: valueFor(row.data, 'generic_name', locale),
      state: row.state.replace(/_/g, ' '),
      validation: row.published_unvalidated
        ? `${row.validation_status} — not clinically reviewed`
        : row.validation_status,
      citations: row.citation_count,
    };
    for (const field of FIELDS) {
      if (field.key === 'generic_name') continue;
      record[field.key] = valueFor(row.data, field.key, locale);
    }
    const added = sheet.addRow(record);
    added.alignment = { vertical: 'top', wrapText: true };
    if (row.published_unvalidated) {
      added.getCell('validation').font = { color: { argb: 'FF9C1F1F' } };
    }
  }

  sheet.autoFilter = { from: 'A1', to: { row: 1, column: sheet.columnCount } };
}
