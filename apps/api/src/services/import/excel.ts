import ExcelJS from 'exceljs';
import JSZip from 'jszip';
import { readFile } from 'node:fs/promises';

export interface SheetSummary {
  name: string;
  rowCount: number;
  headers: string[];
}

export interface ParsedSheet {
  name: string;
  headers: string[];
  /** One object per data row, keyed by header text. */
  rows: Array<Record<string, string | null>>;
  /** Source row numbers, 1-based as shown in Excel. */
  rowNumbers: number[];
}

/**
 * Reads a cell into plain text without interpreting it.
 *
 * Numbers, dates and rich text are converted to a faithful string; nothing is
 * rounded, reformatted or unit-normalised. Errors and formulas keep their
 * displayed value so a reviewer sees what the workbook showed.
 */
export function cellToText(value: ExcelJS.CellValue): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value === 'string') return value;
  if (typeof value === 'number') return String(value);
  if (typeof value === 'boolean') return value ? 'TRUE' : 'FALSE';
  if (value instanceof Date) return value.toISOString().slice(0, 10);

  if (typeof value === 'object') {
    const v = value as unknown as Record<string, unknown>;
    if ('text' in v && typeof v.text === 'string') return v.text;
    if ('richText' in v && Array.isArray(v.richText)) {
      return (v.richText as Array<{ text: string }>).map((p) => p.text).join('');
    }
    if ('hyperlink' in v && typeof v.hyperlink === 'string') {
      return typeof v.text === 'string' ? v.text : v.hyperlink;
    }
    if ('result' in v) return cellToText(v.result as ExcelJS.CellValue);
    if ('error' in v) return String(v.error);
  }
  return String(value);
}

const SPREADSHEETML_NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main';

/** Parts that carry presentation only, never cell values. */
const DECORATIVE_PART = /^xl\/(tables|drawings|charts|pivotTables|pivotCache)\//;

/**
 * Rewrites a workbook into a form the parser reliably accepts.
 *
 * Two real-world quirks are handled:
 *
 * 1. Some generators emit SpreadsheetML with a namespace prefix
 *    (`<x:workbook>` rather than `<workbook>`). That is valid OOXML, but the
 *    parser matches unprefixed element names and would read an empty workbook.
 * 2. Table, drawing and chart parts written by those generators can be
 *    incomplete enough to abort the parse, even though they hold no data.
 *
 * The stored original file is never modified: it stays byte-identical for the
 * audit trail, and only this in-memory working copy is adjusted.
 */
async function normalizeWorkbookParts(buffer: Buffer): Promise<Buffer> {
  const zip = await JSZip.loadAsync(buffer);
  const workbookFile = zip.file('xl/workbook.xml');
  if (!workbookFile) return buffer;

  const workbookXml = await workbookFile.async('string');
  const prefixMatch = new RegExp(
    `xmlns:([A-Za-z0-9_.-]+)\\s*=\\s*["']${SPREADSHEETML_NS}["']`,
  ).exec(workbookXml);

  const removed: string[] = [];
  for (const name of Object.keys(zip.files)) {
    if (DECORATIVE_PART.test(name)) {
      zip.remove(name);
      removed.push(name);
    }
  }

  if (!prefixMatch && removed.length === 0) return buffer;

  const prefix = prefixMatch?.[1];
  const openTag = prefix ? new RegExp(`<${prefix}:`, 'g') : null;
  const closeTag = prefix ? new RegExp(`</${prefix}:`, 'g') : null;
  const nsDecl = prefix
    ? new RegExp(`xmlns:${prefix}\\s*=\\s*["']${SPREADSHEETML_NS}["']`, 'g')
    : null;

  const removedSet = new Set(removed);
  for (const [name, entry] of Object.entries(zip.files)) {
    if (entry.dir) continue;
    const isXml = name.endsWith('.xml') || name.endsWith('.rels');
    if (!isXml) continue;

    let xml = await entry.async('string');
    let changed = false;

    if (openTag && closeTag && nsDecl && xml.includes(`<${prefix}:`)) {
      xml = xml
        .replace(openTag, '<')
        .replace(closeTag, '</')
        .replace(nsDecl, `xmlns="${SPREADSHEETML_NS}"`);
      changed = true;
    }

    // Drop references to the parts that were removed, so the package stays
    // internally consistent.
    if (removedSet.size > 0) {
      if (name.endsWith('.rels')) {
        const cleaned = xml.replace(
          /<Relationship\b[^>]*\/>/g,
          (tag) => (DECORATIVE_PART.test(tag.replace(/^.*Target="\/?/, '').replace(/".*$/, '')) ? '' : tag),
        );
        if (cleaned !== xml) {
          xml = cleaned;
          changed = true;
        }
      }
      if (name === '[Content_Types].xml') {
        const cleaned = xml.replace(
          /<Override\b[^>]*PartName="\/([^"]+)"[^>]*\/>/g,
          (tag, part: string) => (DECORATIVE_PART.test(part) ? '' : tag),
        );
        if (cleaned !== xml) {
          xml = cleaned;
          changed = true;
        }
      }
      // A worksheet's <tableParts>/<drawing> now points at nothing.
      if (name.startsWith('xl/worksheets/') && name.endsWith('.xml')) {
        const cleaned = xml
          .replace(/<tableParts[\s\S]*?<\/tableParts>/g, '')
          .replace(/<tableParts\b[^>]*\/>/g, '')
          .replace(/<drawing\b[^>]*\/>/g, '')
          .replace(/<legacyDrawing\b[^>]*\/>/g, '');
        if (cleaned !== xml) {
          xml = cleaned;
          changed = true;
        }
      }
    }

    if (changed) zip.file(name, xml);
  }

  return zip.generateAsync({ type: 'nodebuffer', compression: 'DEFLATE' });
}

export async function loadWorkbook(path: string): Promise<ExcelJS.Workbook> {
  const raw = await readFile(path);
  const normalized = await normalizeWorkbookParts(raw);
  const workbook = new ExcelJS.Workbook();
  await workbook.xlsx.load(normalized as never);
  if (workbook.worksheets.length === 0) {
    throw new Error('The workbook contains no readable sheets.');
  }
  return workbook;
}

function headerRowOf(sheet: ExcelJS.Worksheet): { rowNumber: number; headers: string[] } {
  // The first row that has at least two non-empty cells is treated as the
  // header row; title banners above it are skipped.
  for (let r = 1; r <= Math.min(sheet.rowCount, 20); r++) {
    const row = sheet.getRow(r);
    const values: string[] = [];
    let filled = 0;
    for (let c = 1; c <= sheet.columnCount; c++) {
      const text = cellToText(row.getCell(c).value)?.trim() ?? '';
      values.push(text);
      if (text) filled++;
    }
    if (filled >= 2) {
      while (values.length && !values[values.length - 1]) values.pop();
      return { rowNumber: r, headers: values };
    }
  }
  return { rowNumber: 1, headers: [] };
}

export function summarizeWorkbook(workbook: ExcelJS.Workbook): SheetSummary[] {
  return workbook.worksheets.map((sheet) => {
    const { rowNumber, headers } = headerRowOf(sheet);
    let rowCount = 0;
    sheet.eachRow({ includeEmpty: false }, (row, r) => {
      if (r <= rowNumber) return;
      const hasValue = headers.some((_, i) => (cellToText(row.getCell(i + 1).value)?.trim() ?? '') !== '');
      if (hasValue) rowCount++;
    });
    return { name: sheet.name, rowCount, headers };
  });
}

export function parseSheet(workbook: ExcelJS.Workbook, sheetName: string): ParsedSheet {
  const sheet = workbook.getWorksheet(sheetName);
  if (!sheet) throw new Error(`Sheet "${sheetName}" not found in workbook`);

  const { rowNumber: headerRow, headers } = headerRowOf(sheet);
  const rows: Array<Record<string, string | null>> = [];
  const rowNumbers: number[] = [];

  sheet.eachRow({ includeEmpty: false }, (row, r) => {
    if (r <= headerRow) return;
    const record: Record<string, string | null> = {};
    let hasValue = false;
    headers.forEach((header, i) => {
      if (!header) return;
      const text = cellToText(row.getCell(i + 1).value);
      const trimmed = text?.trim() ?? null;
      record[header] = trimmed === '' ? null : trimmed;
      if (record[header]) hasValue = true;
    });
    if (!hasValue) return;
    rows.push(record);
    rowNumbers.push(r);
  });

  return { name: sheet.name, headers: headers.filter(Boolean), rows, rowNumbers };
}
