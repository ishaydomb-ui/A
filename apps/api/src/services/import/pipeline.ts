import { createHash } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { fromImportedCell, type Locale, type MedicationData } from '@med/shared';
import { config } from '../../config.js';
import { query, withTransaction, type Queryable } from '../../db/pool.js';
import { badRequest, conflict, notFound } from '../../lib/errors.js';
import type { Actor } from '../catalogue.js';
import { createMedicationDraft, getVersion, openDraftFromPublished, slugify, updateDraft, uniqueSlug } from '../catalogue.js';
import { diffData, type FieldChange } from '../diff.js';
import { loadWorkbook, parseSheet, summarizeWorkbook, type SheetSummary } from './excel.js';
import { runDetectors, type DetectedFinding, type RowContext } from './detectors.js';
import { suggestMapping, validateMapping, type MappingSuggestion } from './mapping.js';

export type RowAction = 'create' | 'update' | 'unchanged' | 'duplicate' | 'skip' | 'error';

export interface BatchRow {
  rowNumber: number;
  slug: string | null;
  name: string;
  action: RowAction;
  matchedMedicationId: string | null;
  diff: FieldChange[];
  error?: string;
}

export interface BatchStats {
  totalRows: number;
  create: number;
  update: number;
  unchanged: number;
  duplicate: number;
  error: number;
  findings: { high: number; medium: number; low: number; info: number };
}

// --- Upload -----------------------------------------------------------------

export interface UploadResult {
  batchId: string;
  sheets: SheetSummary[];
  suggestedSheet: string | null;
  suggestedMapping: MappingSuggestion[];
}

/**
 * Stores the workbook and reads its structure. Nothing is written to the
 * catalogue at this point — the live site is untouched by an upload.
 */
export async function uploadWorkbook(
  file: { filename: string; buffer: Buffer },
  actor: Actor,
): Promise<UploadResult> {
  const sha256 = createHash('sha256').update(file.buffer).digest('hex');

  const { rows: existing } = await query<{ id: string; status: string }>(
    `SELECT id, status FROM import_batches WHERE sha256 = $1 AND status <> 'discarded'`,
    [sha256],
  );
  if (existing[0]) {
    throw conflict('duplicate_upload', 'This exact workbook has already been uploaded.', {
      batchId: existing[0].id,
      status: existing[0].status,
    });
  }

  const dir = resolve(config.importStorageDir);
  await mkdir(dir, { recursive: true });
  // The original file is kept verbatim, named by its own hash, so the audit
  // trail can always point at exactly what was imported.
  const storagePath = `${sha256}.xlsx`;
  await writeFile(join(dir, storagePath), file.buffer);

  const workbook = await loadWorkbook(join(dir, storagePath));
  const sheets = summarizeWorkbook(workbook);

  // Prefer the sheet whose headers map onto the most clinical fields.
  let suggestedSheet: string | null = null;
  let bestScore = 0;
  let suggestedMapping: MappingSuggestion[] = [];
  for (const sheet of sheets) {
    if (sheet.rowCount === 0) continue;
    const mapping = suggestMapping(sheet.headers);
    const score = mapping.filter((m) => m.fieldKey).length;
    if (score > bestScore) {
      bestScore = score;
      suggestedSheet = sheet.name;
      suggestedMapping = mapping;
    }
  }

  const { rows } = await query<{ id: string }>(
    `INSERT INTO import_batches
       (original_filename, storage_path, sha256, byte_size, status, uploaded_by, sheet_name)
     VALUES ($1, $2, $3, $4, 'uploaded', $5, $6) RETURNING id`,
    [file.filename, storagePath, sha256, file.buffer.byteLength, actor.id, suggestedSheet],
  );

  return { batchId: rows[0].id, sheets, suggestedSheet, suggestedMapping };
}

// --- Validate / preview -----------------------------------------------------

export interface BatchRecord {
  id: string;
  original_filename: string;
  storage_path: string;
  sheet_name: string | null;
  status: string;
  column_mapping: Record<string, string | null>;
  locale: Locale;
  source_label: string | null;
  stats: BatchStats | Record<string, never>;
  uploaded_at: Date;
  committed_at: Date | null;
}

export async function getBatch(batchId: string, client?: Queryable): Promise<BatchRecord | null> {
  const { rows } = await query<BatchRecord>('SELECT * FROM import_batches WHERE id = $1', [batchId], client);
  return rows[0] ?? null;
}

/**
 * Maps rows onto clinical fields, compares each against the current
 * catalogue, and runs the data-quality detectors.
 *
 * This is where the "no silent correction" rule is enforced: values are
 * copied verbatim, blanks become an explicit "not supplied", and everything
 * questionable becomes a review finding rather than an edit.
 */
export async function validateBatch(
  batchId: string,
  options: { sheetName: string; mapping: Record<string, string | null>; locale: Locale; sourceLabel?: string },
): Promise<{ rows: BatchRow[]; findings: DetectedFinding[]; stats: BatchStats }> {
  const batch = await getBatch(batchId);
  if (!batch) throw notFound('batch_not_found', 'No such import batch.');
  if (batch.status === 'committed') throw conflict('already_committed', 'This batch has already been committed.');

  const problems = validateMapping(options.mapping);
  if (problems.length > 0) {
    throw badRequest('invalid_mapping', 'The column mapping is not usable.', { problems });
  }

  const workbook = await loadWorkbook(join(resolve(config.importStorageDir), batch.storage_path));
  const sheet = parseSheet(workbook, options.sheetName);

  const contexts: RowContext[] = [];
  const rows: BatchRow[] = [];
  const seenSlugs = new Map<string, number>();

  for (let i = 0; i < sheet.rows.length; i++) {
    const raw = sheet.rows[i];
    const rowNumber = sheet.rowNumbers[i];

    // Map cells onto fields, verbatim, in the declared locale only.
    const data: MedicationData = {};
    for (const [header, fieldKey] of Object.entries(options.mapping)) {
      if (!fieldKey) continue;
      const cell = raw[header];
      const other: Locale = options.locale === 'en' ? 'he' : 'en';
      data[fieldKey] = {
        [options.locale]: fromImportedCell(cell),
        [other]: { state: 'not_supplied', text: null },
      } as MedicationData[string];
    }

    const nameValue = data['generic_name']?.[options.locale];
    const name = nameValue?.state === 'provided' ? (nameValue.text ?? '') : '';

    if (!name) {
      rows.push({
        rowNumber, slug: null, name: `(row ${rowNumber})`, action: 'error',
        matchedMedicationId: null, diff: [],
        error: 'No generic name in this row; it cannot be identified.',
      });
      continue;
    }

    contexts.push({ rowNumber, name, data, raw });

    const slug = slugify(name);
    const firstSeen = seenSlugs.get(slug);
    if (firstSeen !== undefined) {
      rows.push({
        rowNumber, slug, name, action: 'duplicate', matchedMedicationId: null, diff: [],
        error: `Same medication name as row ${firstSeen}.`,
      });
      continue;
    }
    seenSlugs.set(slug, rowNumber);

    // Compare against whatever the catalogue currently holds for this slug.
    const { rows: current } = await query<{ medication_id: string; data: MedicationData }>(
      `SELECT v.medication_id, v.data FROM medication_versions v
         JOIN medications m ON m.id = v.medication_id
        WHERE m.slug = $1 AND v.state IN ('published','approved','in_clinical_review','changes_requested','draft')
        ORDER BY CASE v.state WHEN 'published' THEN 0 ELSE 1 END, v.version_number DESC
        LIMIT 1`,
      [slug],
    );

    if (!current[0]) {
      rows.push({
        rowNumber, slug, name, action: 'create', matchedMedicationId: null,
        diff: diffData({}, data),
      });
    } else {
      const changes = diffData(current[0].data, data);
      rows.push({
        rowNumber, slug, name,
        action: changes.length === 0 ? 'unchanged' : 'update',
        matchedMedicationId: current[0].medication_id,
        diff: changes,
      });
    }
  }

  const findings = runDetectors(contexts);

  const stats: BatchStats = {
    totalRows: sheet.rows.length,
    create: rows.filter((r) => r.action === 'create').length,
    update: rows.filter((r) => r.action === 'update').length,
    unchanged: rows.filter((r) => r.action === 'unchanged').length,
    duplicate: rows.filter((r) => r.action === 'duplicate').length,
    error: rows.filter((r) => r.action === 'error').length,
    findings: {
      high: findings.filter((f) => f.severity === 'high').length,
      medium: findings.filter((f) => f.severity === 'medium').length,
      low: findings.filter((f) => f.severity === 'low').length,
      info: findings.filter((f) => f.severity === 'info').length,
    },
  };

  await withTransaction(async (db) => {
    await query('DELETE FROM import_rows WHERE batch_id = $1', [batchId], db);
    await query('DELETE FROM review_findings WHERE batch_id = $1 AND medication_id IS NULL', [batchId], db);

    for (let i = 0; i < rows.length; i++) {
      const row = rows[i];
      const context = contexts.find((c) => c.rowNumber === row.rowNumber);
      await query(
        `INSERT INTO import_rows
           (batch_id, row_number, raw, mapped, slug, action, matched_medication_id, diff)
         VALUES ($1,$2,$3,$4,$5,$6,$7,$8)`,
        [
          batchId, row.rowNumber,
          JSON.stringify(sheet.rows[sheet.rowNumbers.indexOf(row.rowNumber)] ?? {}),
          JSON.stringify(context?.data ?? {}),
          row.slug, row.action, row.matchedMedicationId, JSON.stringify(row.diff),
        ],
        db,
      );
    }

    for (const finding of findings) {
      await query(
        `INSERT INTO review_findings
           (batch_id, row_number, severity, scope, field_key, issue_type, evidence,
            recommended_action, detector, status)
         VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,'open')`,
        [
          batchId, finding.rowNumber ?? null, finding.severity, finding.scope,
          finding.fieldKey, finding.issueType, finding.evidence,
          finding.recommendedAction, finding.detector,
        ],
        db,
      );
    }

    await query(
      `UPDATE import_batches
          SET status = 'validated', sheet_name = $2, column_mapping = $3, locale = $4,
              source_label = $5, stats = $6, validated_at = now()
        WHERE id = $1`,
      [
        batchId, options.sheetName, JSON.stringify(options.mapping), options.locale,
        options.sourceLabel ?? null, JSON.stringify(stats),
      ],
      db,
    );
  });

  return { rows, findings, stats };
}

// --- Commit -----------------------------------------------------------------

export interface CommitResult {
  created: number;
  updated: number;
  skipped: number;
  versionIds: string[];
}

/**
 * Writes the staged rows into DRAFT versions.
 *
 * Committing an import never publishes anything: every row lands as a draft
 * that still has to pass clinical review, so a new workbook cannot change
 * what a physician sees.
 */
export async function commitBatch(
  batchId: string,
  actor: Actor,
  options: {
    validationStatus?: string;
    sourceDocument?: string;
    /**
     * 'merge' (default) keeps existing content for fields the workbook
     * leaves empty; 'replace' makes the workbook the whole record.
     */
    contentMode?: 'merge' | 'replace';
  } = {},
): Promise<CommitResult> {
  const batch = await getBatch(batchId);
  if (!batch) throw notFound('batch_not_found', 'No such import batch.');
  if (batch.status === 'committed') throw conflict('already_committed', 'This batch has already been committed.');
  if (batch.status !== 'validated') {
    throw conflict('not_validated', 'Validate and review this batch before committing it.');
  }

  const { rows: staged } = await query<{
    id: string; row_number: number; mapped: MedicationData; slug: string;
    action: RowAction; matched_medication_id: string | null;
  }>(
    `SELECT id, row_number, mapped, slug, action, matched_medication_id
       FROM import_rows WHERE batch_id = $1 AND action IN ('create','update') ORDER BY row_number`,
    [batchId],
  );

  const result: CommitResult = { created: 0, updated: 0, skipped: 0, versionIds: [] };
  const validationStatus = options.validationStatus ?? 'Imported — awaiting clinical validation';
  // updateDraft opens its own transaction, so merged content is applied after
  // the staging writes commit. Kept local so concurrent commits cannot mix.
  const pendingUpdates: Array<{ versionId: string; data: MedicationData }> = [];

  for (const row of staged) {
    await withTransaction(async (db) => {
      if (row.action === 'create') {
        const version = await createMedicationDraft(
          {
            slug: await uniqueSlug(row.slug, db),
            data: row.mapped,
            sourceLabel: batch.source_label,
            sourceDocument: options.sourceDocument ?? batch.original_filename,
            validationStatus,
            importBatchId: batchId,
          },
          actor,
          db,
        );
        await query(
          'UPDATE import_rows SET created_version_id = $2 WHERE id = $1',
          [row.id, version.id],
          db,
        );
        result.created++;
        result.versionIds.push(version.id);
      } else if (row.matched_medication_id) {
        // Reuse the open draft if there is one, otherwise branch from the
        // published version. Published content is never written to directly.
        const { rows: open } = await query<{ id: string; state: string }>(
          `SELECT id, state FROM medication_versions
            WHERE medication_id = $1 AND state IN ('draft','changes_requested')`,
          [row.matched_medication_id],
          db,
        );
        let versionId = open[0]?.id;
        if (!versionId) {
          const opened = await openDraftFromPublished(row.matched_medication_id, actor, db);
          versionId = opened.id;
        }

        const current = await getVersion(versionId, db);
        if (!current || (current.state !== 'draft' && current.state !== 'changes_requested')) {
          result.skipped++;
          return;
        }

        // Merge (the default): a field the workbook does not supply must not
        // erase what the catalogue already holds — the right behaviour when a
        // workbook is one contribution among several.
        //
        // Replace: the workbook is the whole record. Used when loading a
        // source of record, where merging would be actively misleading — a
        // cell the clinician left empty would keep showing whatever an older
        // import put there, under a record now attributed entirely to her
        // workbook, with nothing on the page to say which values were hers.
        const merged: MedicationData =
          options.contentMode === 'replace' ? { ...row.mapped } : { ...current.data };
        if (options.contentMode !== 'replace') {
          for (const [fieldKey, localized] of Object.entries(row.mapped)) {
            const incoming = localized[batch.locale];
            if (incoming?.state !== 'provided') continue;
            merged[fieldKey] = { ...(merged[fieldKey] ?? localized), [batch.locale]: incoming } as MedicationData[string];
          }
        }

        // The version branches from the published one, which carries the
        // previous import's provenance — so without this an updated record
        // goes on naming the document it no longer came from. Its content is
        // this batch's, and so is its source.
        await query(
          `UPDATE medication_versions
              SET import_batch_id = $2, source_label = $3, source_document = $4
            WHERE id = $1`,
          [
            versionId,
            batchId,
            batch.source_label,
            options.sourceDocument ?? batch.original_filename,
          ],
          db,
        );
        await query(
          'UPDATE import_rows SET created_version_id = $2 WHERE id = $1',
          [row.id, versionId],
          db,
        );
        result.versionIds.push(versionId);
        result.updated++;
        pendingUpdates.push({ versionId, data: merged });
      }
    });
  }

  for (const pending of pendingUpdates) {
    await updateDraft(pending.versionId, pending.data, actor, { validationStatus });
  }

  // Findings raised during validation are attached to the records they
  // concern, so they keep blocking publication until a reviewer closes them.
  await query(
    `UPDATE review_findings f
        SET medication_id = m.id
       FROM import_rows r
       JOIN medications m ON m.slug = r.slug
      WHERE f.batch_id = $1 AND r.batch_id = $1
        AND f.row_number = r.row_number AND f.medication_id IS NULL`,
    [batchId],
  );

  await query(
    `UPDATE import_batches SET status = 'committed', committed_at = now(), committed_by = $2 WHERE id = $1`,
    [batchId, actor.id],
  );

  return result;
}

export async function discardBatch(batchId: string): Promise<void> {
  const { rowCount } = await query(
    `UPDATE import_batches SET status = 'discarded', discarded_at = now()
      WHERE id = $1 AND status <> 'committed'`,
    [batchId],
  );
  if (!rowCount) throw conflict('cannot_discard', 'A committed batch cannot be discarded.');
}
