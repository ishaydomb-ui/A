import type { FastifyInstance } from 'fastify';
import { z } from 'zod';
import { LOCALES } from '@med/shared';
import { config } from '../config.js';
import { query } from '../db/pool.js';
import { badRequest, notFound } from '../lib/errors.js';
import { audit, auditContext } from '../services/audit.js';
import * as limits from '../services/rateLimit.js';
import * as pipeline from '../services/import/pipeline.js';

function actorOf(req: { currentUser?: { id: string; email: string; role: never } }) {
  const u = req.currentUser!;
  return { id: u.id, email: u.email, role: u.role };
}

export default async function importRoutes(app: FastifyInstance): Promise<void> {
  const canImport = app.requireCapability('import:create');
  const canCommit = app.requireCapability('import:commit');

  app.get('/', { onRequest: [canImport] }, async () => {
    const { rows } = await query(
      `SELECT b.id, b.original_filename, b.sheet_name, b.status, b.locale, b.source_label,
              b.stats, b.uploaded_at, b.committed_at, b.byte_size, b.sha256,
              u.email AS uploaded_by_email
         FROM import_batches b LEFT JOIN users u ON u.id = b.uploaded_by
        ORDER BY b.uploaded_at DESC LIMIT 100`,
    );
    return { batches: rows };
  });

  /**
   * Uploading a workbook only stores and inspects it. The live catalogue is
   * not touched, and nothing is staged until the mapping is confirmed.
   */
  app.post('/', { onRequest: [canImport] }, async (req) => {
    await limits.enforce('import', req.currentUser!.id);
    const file = await req.file();
    if (!file) throw badRequest('no_file', 'Attach a .xlsx workbook to upload.');
    if (!/\.xlsx?$/i.test(file.filename)) {
      throw badRequest('unsupported_file', 'Only .xlsx workbooks are supported.');
    }

    const buffer = await file.toBuffer();
    if (buffer.byteLength > config.maxUploadBytes) {
      throw badRequest('file_too_large', 'That workbook is larger than the configured limit.');
    }

    const result = await pipeline.uploadWorkbook(
      { filename: file.filename, buffer },
      actorOf(req as never),
    );
    await audit({
      ...auditContext(req), action: 'import.uploaded', entityType: 'import_batch',
      entityId: result.batchId,
      detail: { filename: file.filename, bytes: buffer.byteLength, sheets: result.sheets.map((s) => s.name) },
    });
    return result;
  });

  /**
   * Produces the preview: what would change, what is new, what is duplicated,
   * and every data-quality finding. Still nothing written to the catalogue.
   */
  app.post('/:batchId/validate', { onRequest: [canImport] }, async (req) => {
    const { batchId } = z.object({ batchId: z.string().uuid() }).parse(req.params);
    const body = z
      .object({
        sheetName: z.string().min(1).max(200),
        mapping: z.record(z.string(), z.string().nullable()),
        locale: z.enum(LOCALES).default('en'),
        sourceLabel: z.string().max(500).optional(),
      })
      .parse(req.body);

    const result = await pipeline.validateBatch(batchId, body);
    await audit({
      ...auditContext(req), action: 'import.validated', entityType: 'import_batch',
      entityId: batchId, detail: { stats: result.stats },
    });
    return result;
  });

  app.get('/:batchId', { onRequest: [canImport] }, async (req) => {
    const { batchId } = z.object({ batchId: z.string().uuid() }).parse(req.params);
    const batch = await pipeline.getBatch(batchId);
    if (!batch) throw notFound('batch_not_found', 'No such import batch.');

    const { rows } = await query(
      `SELECT row_number, slug, action, matched_medication_id, diff, created_version_id
         FROM import_rows WHERE batch_id = $1 ORDER BY row_number`,
      [batchId],
    );
    const { rows: findings } = await query(
      `SELECT id, row_number, severity, scope, field_key, issue_type, evidence,
              recommended_action, status, detector
         FROM review_findings WHERE batch_id = $1
        ORDER BY CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 WHEN 'low' THEN 2 ELSE 3 END,
                 row_number NULLS LAST`,
      [batchId],
    );
    return { batch, rows, findings };
  });

  /**
   * Commits the staged rows as DRAFTS. Publication still requires clinical
   * review and approval, so importing can never change the live catalogue.
   */
  app.post('/:batchId/commit', { onRequest: [canCommit] }, async (req) => {
    const { batchId } = z.object({ batchId: z.string().uuid() }).parse(req.params);
    const body = z
      .object({
        validationStatus: z.string().max(200).optional(),
        sourceDocument: z.string().max(500).optional(),
        acknowledgeFindings: z.boolean().default(false),
      })
      .parse(req.body ?? {});

    const { rows: open } = await query<{ n: number }>(
      `SELECT count(*)::int AS n FROM review_findings
        WHERE batch_id = $1 AND severity = 'high' AND status = 'open'`,
      [batchId],
    );
    // High-severity findings must be seen before staging is accepted. They
    // continue to block publication regardless of this acknowledgement.
    if ((open[0]?.n ?? 0) > 0 && !body.acknowledgeFindings) {
      throw badRequest(
        'unacknowledged_findings',
        `This batch has ${open[0].n} unresolved high-severity finding(s). ` +
          'Review them and confirm before staging the import.',
        { highSeverityCount: open[0].n },
      );
    }

    const result = await pipeline.commitBatch(batchId, actorOf(req as never), body);
    await audit({
      ...auditContext(req), action: 'import.committed', entityType: 'import_batch',
      entityId: batchId, detail: { ...result },
    });
    return { ...result, note: 'Imported rows are drafts. They are not visible to physicians until published.' };
  });

  app.post('/:batchId/discard', { onRequest: [canImport] }, async (req) => {
    const { batchId } = z.object({ batchId: z.string().uuid() }).parse(req.params);
    await pipeline.discardBatch(batchId);
    await audit({
      ...auditContext(req), action: 'import.discarded', entityType: 'import_batch', entityId: batchId,
    });
    return { status: 'ok' };
  });
}
