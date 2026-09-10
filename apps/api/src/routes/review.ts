import type { FastifyInstance } from 'fastify';
import { z } from 'zod';
import { FINDING_STATUSES, SEVERITIES } from '@med/shared';
import { query } from '../db/pool.js';
import { notFound } from '../lib/errors.js';
import { audit, auditContext } from '../services/audit.js';
import { buildFindingsWorkbook } from '../services/findingsExport.js';

export default async function reviewRoutes(app: FastifyInstance): Promise<void> {
  const canRead = app.requireCapability('review:read');
  const canResolve = app.requireCapability('review:resolve');

  /** The review report: every open question about the imported data. */
  app.get('/findings', { onRequest: [canRead] }, async (req) => {
    const q = z
      .object({
        status: z.enum(FINDING_STATUSES).optional(),
        severity: z.enum(SEVERITIES).optional(),
        batchId: z.string().uuid().optional(),
        medicationSlug: z.string().max(200).optional(),
        limit: z.coerce.number().int().min(1).max(500).default(200),
      })
      .parse(req.query);

    const { rows } = await query(
      `SELECT f.id, f.severity, f.scope, f.field_key, f.issue_type, f.evidence,
              f.recommended_action, f.status, f.detector, f.row_number, f.batch_id,
              f.created_at, f.resolved_at, f.resolution_note,
              m.slug AS medication_slug, u.email AS resolved_by_email
         FROM review_findings f
         LEFT JOIN medications m ON m.id = f.medication_id
         LEFT JOIN users u ON u.id = f.resolved_by
        WHERE ($1::text IS NULL OR f.status::text = $1)
          AND ($2::text IS NULL OR f.severity::text = $2)
          AND ($3::uuid IS NULL OR f.batch_id = $3)
          AND ($4::text IS NULL OR m.slug = $4)
        ORDER BY CASE f.severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 WHEN 'low' THEN 2 ELSE 3 END,
                 f.created_at DESC
        LIMIT $5`,
      [q.status ?? null, q.severity ?? null, q.batchId ?? null, q.medicationSlug ?? null, q.limit],
    );

    const { rows: summary } = await query(
      `SELECT severity, status, count(*)::int AS count
         FROM review_findings GROUP BY severity, status`,
    );
    return { findings: rows, summary };
  });

  /**
   * Every open finding as an Excel workbook, so the gaps can be worked
   * through and corrected in the authoritative source.
   */
  app.get('/findings/export.xlsx', { onRequest: [canRead] }, async (req, reply) => {
    const q = z
      .object({
        status: z
          .string()
          .optional()
          .transform((v) => (v ? v.split(',').filter(Boolean) : undefined)),
      })
      .parse(req.query);

    const workbook = await buildFindingsWorkbook({ status: q.status });
    const buffer = await workbook.xlsx.writeBuffer();

    await audit({ ...auditContext(req), action: 'review.findings_exported' });

    const stamp = new Date().toISOString().slice(0, 10);
    return reply
      .header(
        'content-type',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      )
      .header('content-disposition', `attachment; filename="data-quality-findings-${stamp}.xlsx"`)
      .send(Buffer.from(buffer));
  });

  /** Raise a finding by hand, for something a detector cannot see. */
  app.post('/findings', { onRequest: [canRead] }, async (req) => {
    const body = z
      .object({
        severity: z.enum(SEVERITIES),
        scope: z.string().trim().min(1).max(200),
        fieldKey: z.string().max(100).nullable().optional(),
        issueType: z.string().trim().min(1).max(200),
        evidence: z.string().trim().min(1).max(4000),
        recommendedAction: z.string().trim().min(1).max(2000),
        medicationSlug: z.string().max(200).optional(),
      })
      .parse(req.body);

    let medicationId: string | null = null;
    if (body.medicationSlug) {
      const { rows } = await query<{ id: string }>(
        'SELECT id FROM medications WHERE slug = $1', [body.medicationSlug],
      );
      if (!rows[0]) throw notFound('medication_not_found', 'No such medication.');
      medicationId = rows[0].id;
    }

    const { rows } = await query<{ id: string }>(
      `INSERT INTO review_findings
         (medication_id, severity, scope, field_key, issue_type, evidence,
          recommended_action, status, created_by, detector)
       VALUES ($1,$2,$3,$4,$5,$6,$7,'open',$8,'manual') RETURNING id`,
      [
        medicationId, body.severity, body.scope, body.fieldKey ?? null, body.issueType,
        body.evidence, body.recommendedAction, req.currentUser!.id,
      ],
    );
    await audit({
      ...auditContext(req), action: 'review.finding_raised', entityType: 'review_finding',
      entityId: rows[0].id, detail: { severity: body.severity, issueType: body.issueType },
    });
    return { id: rows[0].id };
  });

  /**
   * Closing a finding is a clinical decision and needs a note explaining it,
   * so the record shows why a flagged discrepancy was accepted.
   */
  app.patch('/findings/:id', { onRequest: [canResolve] }, async (req) => {
    const { id } = z.object({ id: z.string().uuid() }).parse(req.params);
    const body = z
      .object({
        status: z.enum(FINDING_STATUSES),
        resolutionNote: z.string().trim().max(2000).optional(),
      })
      .parse(req.body);

    const closing = body.status === 'resolved' || body.status === 'wont_fix';
    if (closing && !body.resolutionNote) {
      const { badRequest } = await import('../lib/errors.js');
      throw badRequest('note_required', 'Explain how this finding was resolved.');
    }

    const { rows } = await query(
      `UPDATE review_findings
          SET status = $2,
              resolution_note = COALESCE($3, resolution_note),
              resolved_at = CASE WHEN $2 IN ('resolved','wont_fix') THEN now() ELSE NULL END,
              resolved_by = CASE WHEN $2 IN ('resolved','wont_fix') THEN $4::uuid ELSE NULL END
        WHERE id = $1 RETURNING id, status`,
      [id, body.status, body.resolutionNote ?? null, req.currentUser!.id],
    );
    if (!rows[0]) throw notFound('finding_not_found', 'No such finding.');

    await audit({
      ...auditContext(req), action: 'review.finding_updated', entityType: 'review_finding',
      entityId: id, detail: { status: body.status, note: body.resolutionNote ?? null },
    });
    return { finding: rows[0] };
  });

  /** Work queue: everything waiting for a clinical reviewer. */
  app.get('/queue', { onRequest: [app.requireCapability('catalogue:read_unpublished')] }, async () => {
    const { rows } = await query(
      `SELECT v.id AS version_id, v.version_number, v.state, v.validation_status,
              v.submitted_at, m.slug,
              v.data #>> '{generic_name,en,text}' AS name_en,
              v.data #>> '{generic_name,he,text}' AS name_he,
              (SELECT count(*)::int FROM review_findings f
                WHERE f.medication_id = m.id AND f.status IN ('open','acknowledged')
                  AND f.severity = 'high') AS blocking_findings
         FROM medication_versions v JOIN medications m ON m.id = v.medication_id
        WHERE v.state IN ('in_clinical_review', 'approved', 'changes_requested', 'draft')
        ORDER BY CASE v.state
                   WHEN 'in_clinical_review' THEN 0 WHEN 'changes_requested' THEN 1
                   WHEN 'approved' THEN 2 ELSE 3 END,
                 v.submitted_at NULLS LAST`,
    );
    return { queue: rows };
  });
}
