import type { FastifyInstance } from 'fastify';
import { z } from 'zod';
import {
  APPROVAL_STATUSES, FIELD_KEYS, LOCALES, VALUE_STATES, WORKFLOW_STATES,
  allowedTransitions, resolveField, type MedicationData, type WorkflowState,
} from '@med/shared';
import { query } from '../db/pool.js';
import { notFound } from '../lib/errors.js';
import { assertCapability } from '../plugins/auth.js';
import { audit, auditContext } from '../services/audit.js';
import * as catalogue from '../services/catalogue.js';
import * as limits from '../services/rateLimit.js';

const fieldValueSchema = z.object({
  state: z.enum(VALUE_STATES as unknown as [string, ...string[]]),
  text: z.string().max(20_000).nullable(),
});

/**
 * Clinical content is accepted only for known field keys and known locales.
 * Text is stored exactly as submitted — no trimming of meaningful content, no
 * unit normalisation, no spelling correction.
 */
const dataSchema = z.record(
  z.string().refine((k) => FIELD_KEYS.includes(k), { message: 'unknown field key' }),
  z.object({
    en: fieldValueSchema,
    he: fieldValueSchema,
  }),
);

const citationSchema = z.object({
  fieldKey: z.string().refine((k) => FIELD_KEYS.includes(k)),
  title: z.string().trim().min(1).max(500),
  documentRef: z.string().max(500).nullable().optional(),
  url: z.string().url().max(2000).nullable().optional(),
  page: z.string().max(50).nullable().optional(),
  jurisdiction: z.string().max(50).nullable().optional(),
  approvalStatus: z.enum(APPROVAL_STATUSES as unknown as [string, ...string[]]),
  reviewedAt: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).nullable().optional(),
});

function actorOf(req: { currentUser?: { id: string; email: string; role: never } }) {
  const u = req.currentUser!;
  return { id: u.id, email: u.email, role: u.role };
}

export default async function catalogueRoutes(app: FastifyInstance): Promise<void> {
  const readPublished = app.requireCapability('catalogue:read_published');
  const readUnpublished = app.requireCapability('catalogue:read_unpublished');
  const editDraft = app.requireCapability('catalogue:edit_draft');

  /**
   * A physician sees the published version and nothing else. Draft content is
   * unreachable through this route regardless of the query string.
   */
  app.get('/:slug', { onRequest: [readPublished] }, async (req) => {
    const { slug } = z.object({ slug: z.string().min(1).max(200) }).parse(req.params);
    const q = z.object({ locale: z.enum(LOCALES).default('en'), draft: z.coerce.boolean().default(false) })
      .parse(req.query);

    const wantsDraft = q.draft;
    // Reading unpublished content needs its own capability, checked here
    // rather than inferred from the route.
    if (wantsDraft) assertCapability(req.currentUser, 'catalogue:read_unpublished');

    const version = wantsDraft
      ? (await catalogue.getWorkingBySlug(slug)) ?? (await catalogue.getPublishedBySlug(slug))
      : await catalogue.getPublishedBySlug(slug);
    if (!version) throw notFound('medication_not_found', 'No such medication.');

    const citations = await catalogue.getCitations(version.id);
    const fields = Object.fromEntries(
      FIELD_KEYS.map((key) => [key, resolveField(version.data, key, q.locale)]),
    );

    return {
      slug: version.slug,
      versionId: version.id,
      versionNumber: version.version_number,
      state: version.state,
      validationStatus: version.validation_status,
      reviewedAt: version.reviewed_at?.toISOString().slice(0, 10) ?? null,
      source: {
        label: version.source_label,
        document: version.source_document,
        version: version.source_version,
      },
      publishedAt: version.published_at?.toISOString() ?? null,
      fields,
      raw: version.data,
      citations,
    };
  });

  /** Everything a physician may read, for the compare view. */
  app.post('/compare', { onRequest: [readPublished] }, async (req) => {
    const body = z
      .object({
        slugs: z.array(z.string().min(1).max(200)).min(2).max(3),
        locale: z.enum(LOCALES).default('en'),
      })
      .parse(req.body);

    const results = [];
    for (const slug of body.slugs) {
      const version = await catalogue.getPublishedBySlug(slug);
      if (!version) throw notFound('medication_not_found', `No published medication "${slug}".`);
      results.push({
        slug,
        versionId: version.id,
        fields: Object.fromEntries(
          FIELD_KEYS.map((key) => [key, resolveField(version.data, key, body.locale)]),
        ),
      });
    }
    return { medications: results };
  });

  app.get('/:slug/revisions', { onRequest: [readUnpublished] }, async (req) => {
    const { slug } = z.object({ slug: z.string().min(1).max(200) }).parse(req.params);
    const { rows } = await query<{ id: string }>('SELECT id FROM medications WHERE slug = $1', [slug]);
    if (!rows[0]) throw notFound('medication_not_found', 'No such medication.');
    return {
      revisions: await catalogue.getRevisions(rows[0].id),
      versions: (await catalogue.listVersions(rows[0].id)).map((v) => ({
        id: v.id,
        versionNumber: v.version_number,
        state: v.state,
        validationStatus: v.validation_status,
        createdAt: v.created_at.toISOString(),
        publishedAt: v.published_at?.toISOString() ?? null,
        importBatchId: v.import_batch_id,
      })),
    };
  });

  app.post('/', { onRequest: [editDraft] }, async (req) => {
    const body = z
      .object({
        data: dataSchema,
        sourceLabel: z.string().max(500).optional(),
        sourceDocument: z.string().max(500).optional(),
        sourceVersion: z.string().max(100).optional(),
        validationStatus: z.string().max(200).optional(),
        reviewedAt: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(),
      })
      .parse(req.body);
    await limits.enforce('mutation', req.currentUser!.id);

    const version = await catalogue.createMedicationDraft(
      { ...body, data: body.data as MedicationData },
      actorOf(req as never),
    );
    await audit({
      ...auditContext(req), action: 'catalogue.created', entityType: 'medication',
      entityId: version.medication_id, detail: { slug: version.slug, versionId: version.id },
    });
    return { version: { id: version.id, slug: version.slug, state: version.state } };
  });

  app.post('/:slug/draft', { onRequest: [editDraft] }, async (req) => {
    const { slug } = z.object({ slug: z.string().min(1).max(200) }).parse(req.params);
    const { rows } = await query<{ id: string }>('SELECT id FROM medications WHERE slug = $1', [slug]);
    if (!rows[0]) throw notFound('medication_not_found', 'No such medication.');
    const version = await catalogue.openDraftFromPublished(rows[0].id, actorOf(req as never));
    await audit({
      ...auditContext(req), action: 'catalogue.draft_opened', entityType: 'medication',
      entityId: rows[0].id, detail: { versionId: version.id },
    });
    return { version: { id: version.id, slug: version.slug, state: version.state } };
  });

  app.patch('/versions/:versionId', { onRequest: [editDraft] }, async (req) => {
    const { versionId } = z.object({ versionId: z.string().uuid() }).parse(req.params);
    const body = z
      .object({
        data: dataSchema,
        sourceLabel: z.string().max(500).nullable().optional(),
        sourceDocument: z.string().max(500).nullable().optional(),
        sourceVersion: z.string().max(100).nullable().optional(),
        validationStatus: z.string().max(200).nullable().optional(),
        reviewedAt: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).nullable().optional(),
      })
      .parse(req.body);
    await limits.enforce('mutation', req.currentUser!.id);

    const { version, changes } = await catalogue.updateDraft(
      versionId, body.data as MedicationData, actorOf(req as never), body,
    );
    await audit({
      ...auditContext(req), action: 'catalogue.edited', entityType: 'medication_version',
      entityId: versionId, detail: { changedFields: changes.map((c) => `${c.fieldKey}:${c.locale}`) },
    });
    return { version: { id: version.id, state: version.state }, changes };
  });

  /** Transitions are authorised by the shared state machine, server-side. */
  app.post('/versions/:versionId/transition', { onRequest: [app.requireAuth] }, async (req) => {
    const { versionId } = z.object({ versionId: z.string().uuid() }).parse(req.params);
    const body = z
      .object({
        to: z.enum(WORKFLOW_STATES as unknown as [WorkflowState, ...WorkflowState[]]),
        reason: z.string().max(2000).nullable().optional(),
      })
      .parse(req.body);
    await limits.enforce('mutation', req.currentUser!.id);

    const version = await catalogue.transitionVersion(
      versionId, body.to, actorOf(req as never), body.reason ?? null,
    );
    await audit({
      ...auditContext(req), action: `catalogue.${body.to}`, entityType: 'medication_version',
      entityId: versionId, detail: { to: body.to, reason: body.reason ?? null },
    });
    return { version: { id: version.id, state: version.state } };
  });

  /** Tells the UI which buttons to offer, and why publishing is blocked. */
  app.get('/versions/:versionId/workflow', { onRequest: [readUnpublished] }, async (req) => {
    const { versionId } = z.object({ versionId: z.string().uuid() }).parse(req.params);
    const version = await catalogue.getVersion(versionId);
    if (!version) throw notFound('version_not_found', 'No such version.');
    return {
      state: version.state,
      allowed: allowedTransitions(req.currentUser!.role, version.state).map((t) => ({
        to: t.to,
        reasonRequired: t.reasonRequired,
      })),
      publishBlockers: await catalogue.publishBlockers(versionId),
    };
  });

  app.post('/versions/:versionId/citations', { onRequest: [editDraft] }, async (req) => {
    const { versionId } = z.object({ versionId: z.string().uuid() }).parse(req.params);
    const body = citationSchema.parse(req.body);
    const citation = await catalogue.addCitation(versionId, body as never, actorOf(req as never));
    await audit({
      ...auditContext(req), action: 'catalogue.citation_added',
      entityType: 'medication_version', entityId: versionId,
      detail: { fieldKey: body.fieldKey, title: body.title },
    });
    return { citation };
  });

  app.delete('/versions/:versionId/citations/:citationId', { onRequest: [editDraft] }, async (req) => {
    const params = z
      .object({ versionId: z.string().uuid(), citationId: z.string().uuid() })
      .parse(req.params);
    await catalogue.removeCitation(params.versionId, params.citationId);
    await audit({
      ...auditContext(req), action: 'catalogue.citation_removed',
      entityType: 'medication_version', entityId: params.versionId,
      detail: { citationId: params.citationId },
    });
    return { status: 'ok' };
  });

  /** Aliases feed the search index; they are metadata, not clinical claims. */
  app.post('/:slug/aliases', { onRequest: [editDraft] }, async (req) => {
    const { slug } = z.object({ slug: z.string().min(1).max(200) }).parse(req.params);
    const body = z
      .object({
        alias: z.string().trim().min(1).max(200),
        kind: z.enum(['trade_name', 'abbreviation', 'hebrew_name', 'synonym', 'misspelling']),
      })
      .parse(req.body);
    const { rows } = await query<{ id: string }>('SELECT id FROM medications WHERE slug = $1', [slug]);
    if (!rows[0]) throw notFound('medication_not_found', 'No such medication.');

    const { normalizeText } = await import('@med/shared');
    await query(
      `INSERT INTO medication_aliases (medication_id, alias, alias_normalized, kind, created_by)
       VALUES ($1, $2, $3, $4, $5)
       ON CONFLICT (medication_id, alias_normalized, kind) DO NOTHING`,
      [rows[0].id, body.alias, normalizeText(body.alias), body.kind, req.currentUser!.id],
    );
    await audit({
      ...auditContext(req), action: 'catalogue.alias_added', entityType: 'medication',
      entityId: rows[0].id, detail: body,
    });
    return { status: 'ok' };
  });
}
