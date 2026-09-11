import ExcelJS from 'exceljs';
import { getField } from '@med/shared';
import { query } from '../db/pool.js';

/**
 * Builds a workbook of every open data-quality finding, so the gaps can be
 * worked through and corrected in the authoritative Excel.
 *
 * Each row carries the value the catalogue currently holds alongside the
 * problem, because a list of complaints without the text they refer to is not
 * actionable. A blank "Correction" column is left for the reviewer to fill in.
 */
interface FindingRow {
  id: string;
  severity: string;
  scope: string;
  field_key: string | null;
  issue_type: string;
  evidence: string;
  recommended_action: string;
  status: string;
  detector: string | null;
  row_number: number | null;
  medication_slug: string | null;
  current_value: string | null;
  generic_name: string | null;
}

const SEVERITY_ORDER = { high: 0, medium: 1, low: 2, info: 3 } as const;

const HEADER_FILL: ExcelJS.Fill = {
  type: 'pattern',
  pattern: 'solid',
  fgColor: { argb: 'FF1F3B57' },
};

const SEVERITY_FILL: Record<string, string> = {
  high: 'FFF8D7DA',
  medium: 'FFFDF0DA',
  low: 'FFF1F3F5',
  info: 'FFF1F3F5',
};

export async function buildFindingsWorkbook(options: {
  status?: string[];
} = {}): Promise<ExcelJS.Workbook> {
  const statuses = options.status ?? ['open', 'acknowledged'];

  const { rows } = await query<FindingRow>(
    `SELECT f.id, f.severity::text AS severity, f.scope, f.field_key,
            f.issue_type, f.evidence, f.recommended_action,
            f.status::text AS status, f.detector, f.row_number,
            m.slug AS medication_slug,
            v.data #>> ARRAY['generic_name', 'en', 'text'] AS generic_name,
            CASE WHEN f.field_key IS NULL THEN NULL
                 ELSE v.data #>> ARRAY[f.field_key, 'en', 'text'] END AS current_value
       FROM review_findings f
       LEFT JOIN medications m ON m.id = f.medication_id
       LEFT JOIN LATERAL (
         SELECT data FROM medication_versions
          WHERE medication_id = f.medication_id
          ORDER BY version_number DESC LIMIT 1
       ) v ON true
      WHERE f.status::text = ANY($1::text[])
      ORDER BY CASE f.severity::text
                 WHEN 'high' THEN 0 WHEN 'medium' THEN 1 WHEN 'low' THEN 2 ELSE 3 END,
               f.scope, f.field_key NULLS FIRST`,
    [statuses],
  );

  const workbook = new ExcelJS.Workbook();
  workbook.creator = 'Medication Catalogue';
  workbook.created = new Date();

  buildSummarySheet(workbook, rows);
  buildFindingsSheet(workbook, rows);
  buildByMedicationSheet(workbook, rows);

  return workbook;
}

function styleHeader(sheet: ExcelJS.Worksheet): void {
  const header = sheet.getRow(1);
  header.font = { bold: true, color: { argb: 'FFFFFFFF' }, size: 11 };
  header.fill = HEADER_FILL;
  header.alignment = { vertical: 'middle' };
  header.height = 22;
  sheet.views = [{ state: 'frozen', ySplit: 1 }];
}

function buildSummarySheet(workbook: ExcelJS.Workbook, rows: FindingRow[]): void {
  const sheet = workbook.addWorksheet('Summary');
  sheet.columns = [
    { header: 'Item', key: 'item', width: 46 },
    { header: 'Count', key: 'count', width: 12 },
    { header: 'Note', key: 'note', width: 80 },
  ];
  styleHeader(sheet);

  const bySeverity = (severity: string) => rows.filter((r) => r.severity === severity).length;
  const byDetector = new Map<string, number>();
  for (const row of rows) {
    const key = row.detector ?? 'manual';
    byDetector.set(key, (byDetector.get(key) ?? 0) + 1);
  }

  sheet.addRows([
    { item: 'Total open findings', count: rows.length, note: 'Every item awaiting a decision.' },
    { item: 'High severity', count: bySeverity('high'), note: 'Blocks publication until closed.' },
    { item: 'Medium severity', count: bySeverity('medium'), note: 'Should be resolved before clinical use.' },
    { item: 'Low severity', count: bySeverity('low'), note: 'Formatting and consistency.' },
    { item: '', count: '' as never, note: '' },
    { item: 'By detector', count: '' as never, note: '' },
  ]);

  for (const [detector, count] of [...byDetector].sort((a, b) => b[1] - a[1])) {
    sheet.addRow({ item: `  ${detector.replace(/_/g, ' ')}`, count, note: '' });
  }

  sheet.addRow({});
  const guidance = sheet.addRow({
    item: 'How to use this workbook',
    count: '' as never,
    note:
      'Findings lists every gap. Fix the underlying data in the authoritative Excel, then re-import. ' +
      'Nothing here has been corrected automatically.',
  });
  guidance.font = { bold: true };
}

function buildFindingsSheet(workbook: ExcelJS.Workbook, rows: FindingRow[]): void {
  const sheet = workbook.addWorksheet('Findings');
  sheet.columns = [
    { header: 'Severity', key: 'severity', width: 11 },
    { header: 'Medication', key: 'medication', width: 26 },
    { header: 'Field', key: 'field', width: 22 },
    { header: 'Issue', key: 'issue', width: 26 },
    { header: 'Current value in catalogue', key: 'current', width: 46 },
    { header: 'What is wrong', key: 'evidence', width: 62 },
    { header: 'Recommended action', key: 'action', width: 52 },
    { header: 'Correction (fill in)', key: 'correction', width: 34 },
    { header: 'Source row', key: 'row', width: 11 },
  ];
  styleHeader(sheet);

  for (const row of rows) {
    const field = row.field_key ? getField(row.field_key) : undefined;
    const added = sheet.addRow({
      severity: row.severity,
      medication: row.generic_name ?? row.scope,
      field: field?.label.en ?? row.field_key ?? '(whole record)',
      issue: row.issue_type,
      current: row.current_value ?? '(nothing recorded)',
      evidence: row.evidence,
      action: row.recommended_action,
      correction: '',
      row: row.row_number ?? '',
    });
    added.alignment = { vertical: 'top', wrapText: true };
    const fill = SEVERITY_FILL[row.severity];
    if (fill) {
      added.getCell('severity').fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: fill } };
    }
    added.getCell('severity').font = { bold: row.severity === 'high' };
    // The column the reviewer types into, marked so it is obvious.
    added.getCell('correction').fill = {
      type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFFFFDE7' },
    };
  }

  sheet.autoFilter = { from: 'A1', to: { row: 1, column: sheet.columnCount } };
}

function buildByMedicationSheet(workbook: ExcelJS.Workbook, rows: FindingRow[]): void {
  const sheet = workbook.addWorksheet('By medication');
  sheet.columns = [
    { header: 'Medication', key: 'medication', width: 30 },
    { header: 'High', key: 'high', width: 8 },
    { header: 'Medium', key: 'medium', width: 9 },
    { header: 'Low', key: 'low', width: 8 },
    { header: 'Issues', key: 'issues', width: 90 },
  ];
  styleHeader(sheet);

  const byMedication = new Map<string, FindingRow[]>();
  for (const row of rows) {
    const key = row.generic_name ?? row.scope;
    const list = byMedication.get(key) ?? [];
    list.push(row);
    byMedication.set(key, list);
  }

  const ordered = [...byMedication].sort((a, b) => {
    const severity = (list: FindingRow[]) =>
      Math.min(...list.map((r) => SEVERITY_ORDER[r.severity as keyof typeof SEVERITY_ORDER] ?? 3));
    return severity(a[1]) - severity(b[1]) || b[1].length - a[1].length;
  });

  for (const [medication, list] of ordered) {
    const added = sheet.addRow({
      medication,
      high: list.filter((r) => r.severity === 'high').length,
      medium: list.filter((r) => r.severity === 'medium').length,
      low: list.filter((r) => r.severity === 'low').length,
      issues: [...new Set(list.map((r) => r.issue_type))].join('; '),
    });
    added.alignment = { vertical: 'top', wrapText: true };
  }

  sheet.autoFilter = { from: 'A1', to: { row: 1, column: sheet.columnCount } };
}
