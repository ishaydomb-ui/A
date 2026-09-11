import {
  CITATION_REQUIRED_FIELDS, checkTransition, normalizeText,
  type Citation, type MedicationData, type Role, type WorkflowState,
} from '@med/shared';
import { query, withTransaction, type Queryable } from '../db/pool.js';
import { badRequest, conflict, forbidden, notFound } from '../lib/errors.js';
import { diffData, type FieldChange } from './diff.js';
import { indexVersion, reindexState } from './indexer.js';
import { unvalidatedPublicationAllowed } from './settings.js';

export interface VersionRow {
  id: string;
  medication_id: string;
  version_number: number;
  state: WorkflowState;
  data: MedicationData;
  published_unvalidated: boolean;
  overridden_blockers: PublishBlocker[];
  source_label: string | null;
  source_document: string | null;
  source_version: string | null;
  reviewed_at: Date | null;
  validation_status: string;
  import_batch_id: string | null;
  created_at: Date;
  created_by: string | null;
  approved_at: Date | null;
  approved_by: string | null;
  published_at: Date | null;
  published_by: string | null;
  change_reason: string | null;
  slug?: string;
}

export interface Actor {
  id: string;
  email: string;
  role: Role;
}

export function slugify(name: string): string {
  const normalized = normalizeText(name).replace(/\s+/g, '-');
  return normalized || 'medication';
}

/** Makes a slug unique by appending -2, -3, ... when needed. */
export async function uniqueSlug(base: string, client?: Queryable): Promise<string> {
  let candidate = base;
  for (let n = 2; ; n++) {
    const { rows } = await query('SELECT 1 FROM medications WHERE slug = $1', [candidate], client);
    if (rows.length === 0) return candidate;
    candidate = `${base}-${n}`;
  }
}

// --- Reads ------------------------------------------------------------------

export async function getPublishedBySlug(slug: string): Promise<VersionRow | null> {
  const { rows } = await query<VersionRow>(
    `SELECT v.*, m.slug FROM medication_versions v
       JOIN medications m ON m.id = v.medication_id
      WHERE m.slug = $1 AND v.state = 'published'`,
    [slug],
  );
  return rows[0] ?? null;
}

/** Returns the version a privileged user is working on, published or not. */
export async function getWorkingBySlug(slug: string): Promise<VersionRow | null> {
  const { rows } = await query<VersionRow>(
    `SELECT v.*, m.slug FROM medication_versions v
       JOIN medications m ON m.id = v.medication_id
      WHERE m.slug = $1
        AND v.state IN ('draft', 'in_clinical_review', 'changes_requested', 'approved')
      ORDER BY v.version_number DESC LIMIT 1`,
    [slug],
  );
  return rows[0] ?? null;
}

export async function getVersion(versionId: string, client?: Queryable): Promise<VersionRow | null> {
  const { rows } = await query<VersionRow>(
    `SELECT v.*, m.slug FROM medication_versions v
       JOIN medications m ON m.id = v.medication_id
      WHERE v.id = $1`,
    [versionId],
    client,
  );
  return rows[0] ?? null;
}

export async function listVersions(medicationId: string): Promise<VersionRow[]> {
  const { rows } = await query<VersionRow>(
    `SELECT v.*, m.slug FROM medication_versions v
       JOIN medications m ON m.id = v.medication_id
      WHERE v.medication_id = $1 ORDER BY v.version_number DESC`,
    [medicationId],
  );
  return rows;
}

export async function getRevisions(medicationId: string): Promise<unknown[]> {
  const { rows } = await query(
    `SELECT r.id, r.occurred_at, r.actor_email, r.action, r.from_state, r.to_state,
            r.reason, r.field_diff, r.version_id, v.version_number
       FROM revisions r
       LEFT JOIN medication_versions v ON v.id = r.version_id
      WHERE r.medication_id = $1
      ORDER BY r.id DESC`,
    [medicationId],
  );
  return rows;
}

export async function getCitations(versionId: string, client?: Queryable): Promise<Citation[]> {
  const { rows } = await query<{
    id: string; field_key: string; title: string; document_ref: string | null;
    url: string | null; page: string | null; jurisdiction: string | null;
    approval_status: Citation['approvalStatus']; reviewed_at: Date | null;
  }>(
    `SELECT id, field_key, title, document_ref, url, page, jurisdiction, approval_status, reviewed_at
       FROM citations WHERE version_id = $1 ORDER BY field_key, created_at`,
    [versionId],
    client,
  );
  return rows.map((r) => ({
    id: r.id,
    fieldKey: r.field_key,
    title: r.title,
    documentRef: r.document_ref,
    url: r.url,
    page: r.page,
    jurisdiction: r.jurisdiction,
    approvalStatus: r.approval_status,
    reviewedAt: r.reviewed_at ? r.reviewed_at.toISOString().slice(0, 10) : null,
  }));
}

async function aliasesFor(medicationId: string, client?: Queryable): Promise<string[]> {
  const { rows } = await query<{ alias: string }>(
    'SELECT alias FROM medication_aliases WHERE medication_id = $1',
    [medicationId],
    client,
  );
  return rows.map((r) => r.alias);
}

// --- Writes -----------------------------------------------------------------

export interface CreateDraftInput {
  slug?: string;
  data: MedicationData;
  sourceLabel?: string | null;
  sourceDocument?: string | null;
  sourceVersion?: string | null;
  validationStatus?: string;
  reviewedAt?: string | null;
  importBatchId?: string | null;
}

/** Creates a new medication together with its first draft version. */
export async function createMedicationDraft(
  input: CreateDraftInput,
  actor: Actor,
  client?: Queryable,
): Promise<VersionRow> {
  const run = async (db: Queryable): Promise<VersionRow> => {
    const nameField = input.data['generic_name'];
    const name = nameField?.en?.text ?? nameField?.he?.text ?? null;
    if (!input.slug && !name) {
      throw badRequest('missing_name', 'A medication needs a generic name before it can be created.');
    }
    const slug = await uniqueSlug(input.slug ?? slugify(name!), db);

    const { rows: med } = await query<{ id: string }>(
      'INSERT INTO medications (slug, created_by) VALUES ($1, $2) RETURNING id',
      [slug, actor.id],
      db,
    );
    const medicationId = med[0].id;

    const { rows } = await query<VersionRow>(
      `INSERT INTO medication_versions
         (medication_id, version_number, state, data, source_label, source_document,
          source_version, validation_status, reviewed_at, import_batch_id, created_by)
       VALUES ($1, 1, 'draft', $2, $3, $4, $5, $6, $7, $8, $9)
       RETURNING *`,
      [
        medicationId,
        JSON.stringify(input.data),
        input.sourceLabel ?? null,
        input.sourceDocument ?? null,
        input.sourceVersion ?? null,
        input.validationStatus ?? 'Unvalidated website snapshot',
        input.reviewedAt ?? null,
        input.importBatchId ?? null,
        actor.id,
      ],
      db,
    );
    const version = { ...rows[0], slug };

    await recordRevision(
      { medicationId, versionId: version.id, actor, action: 'created', toState: 'draft',
        fieldDiff: diffData({}, input.data) },
      db,
    );
    await indexVersion(version.id, medicationId, slug, 'draft', input.data, await aliasesFor(medicationId, db), db);
    return version;
  };
  return client ? run(client) : withTransaction(run);
}

/**
 * Opens a new draft from the currently published version so that published
 * content is never edited in place.
 */
export async function openDraftFromPublished(
  medicationId: string,
  actor: Actor,
  client?: Queryable,
): Promise<VersionRow> {
  const run = async (db: Queryable): Promise<VersionRow> => {
    const { rows: existing } = await query<VersionRow>(
      `SELECT v.*, m.slug FROM medication_versions v JOIN medications m ON m.id = v.medication_id
        WHERE v.medication_id = $1
          AND v.state IN ('draft','in_clinical_review','changes_requested','approved')`,
      [medicationId],
      db,
    );
    if (existing[0]) return existing[0];

    const { rows: published } = await query<VersionRow>(
      `SELECT v.*, m.slug FROM medication_versions v JOIN medications m ON m.id = v.medication_id
        WHERE v.medication_id = $1 AND v.state = 'published'`,
      [medicationId],
      db,
    );
    const source = published[0];
    if (!source) throw notFound('no_published_version', 'That medication has no published version.');

    const { rows: next } = await query<{ n: number }>(
      'SELECT COALESCE(max(version_number), 0) + 1 AS n FROM medication_versions WHERE medication_id = $1',
      [medicationId],
      db,
    );

    const { rows } = await query<VersionRow>(
      `INSERT INTO medication_versions
         (medication_id, version_number, state, data, source_label, source_document,
          source_version, validation_status, reviewed_at, created_by)
       VALUES ($1, $2, 'draft', $3, $4, $5, $6, $7, $8, $9) RETURNING *`,
      [
        medicationId, next[0].n, JSON.stringify(source.data), source.source_label,
        source.source_document, source.source_version, source.validation_status,
        source.reviewed_at, actor.id,
      ],
      db,
    );
    const version = { ...rows[0], slug: source.slug };

    // Citations travel with the content they support.
    await query(
      `INSERT INTO citations (version_id, field_key, title, document_ref, url, page,
                              jurisdiction, approval_status, reviewed_at, created_by)
       SELECT $1, field_key, title, document_ref, url, page, jurisdiction, approval_status,
              reviewed_at, $2
         FROM citations WHERE version_id = $3`,
      [version.id, actor.id, source.id],
      db,
    );
    await recordRevision(
      { medicationId, versionId: version.id, actor, action: 'draft_opened', fromState: 'published',
        toState: 'draft', fieldDiff: [] },
      db,
    );
    await indexVersion(version.id, medicationId, source.slug!, 'draft', source.data, await aliasesFor(medicationId, db), db);
    return version;
  };
  return client ? run(client) : withTransaction(run);
}

export async function updateDraft(
  versionId: string,
  data: MedicationData,
  actor: Actor,
  meta?: { sourceLabel?: string | null; sourceDocument?: string | null; sourceVersion?: string | null;
           validationStatus?: string | null; reviewedAt?: string | null },
): Promise<{ version: VersionRow; changes: FieldChange[] }> {
  return withTransaction(async (db) => {
    const current = await getVersion(versionId, db);
    if (!current) throw notFound('version_not_found', 'No such version.');
    if (current.state !== 'draft' && current.state !== 'changes_requested') {
      throw conflict('not_editable', `A version in state "${current.state}" cannot be edited.`);
    }

    const changes = diffData(current.data, data);
    const { rows } = await query<VersionRow>(
      `UPDATE medication_versions
          SET data = $2,
              source_label      = COALESCE($3, source_label),
              source_document   = COALESCE($4, source_document),
              source_version    = COALESCE($5, source_version),
              validation_status = COALESCE($6, validation_status),
              reviewed_at       = COALESCE($7, reviewed_at)
        WHERE id = $1 RETURNING *`,
      [
        versionId, JSON.stringify(data), meta?.sourceLabel ?? null, meta?.sourceDocument ?? null,
        meta?.sourceVersion ?? null, meta?.validationStatus ?? null, meta?.reviewedAt ?? null,
      ],
      db,
    );

    if (changes.length > 0) {
      await recordRevision(
        { medicationId: current.medication_id, versionId, actor, action: 'edited', fieldDiff: changes },
        db,
      );
    }
    await indexVersion(
      versionId, current.medication_id, current.slug!, current.state, data,
      await aliasesFor(current.medication_id, db), db,
    );
    return { version: { ...rows[0], slug: current.slug }, changes };
  });
}

// --- Workflow ---------------------------------------------------------------

export interface PublishBlocker {
  code: string;
  message: string;
  fieldKey?: string;
}

/**
 * Everything that must be true before content reaches a physician.
 * Returned as a list so the UI can show all of it at once rather than
 * revealing one blocker at a time.
 */
export async function publishBlockers(versionId: string, client?: Queryable): Promise<PublishBlocker[]> {
  const version = await getVersion(versionId, client);
  if (!version) return [{ code: 'not_found', message: 'No such version.' }];
  const blockers: PublishBlocker[] = [];

  if (!version.approved_by) {
    blockers.push({
      code: 'not_clinically_approved',
      message: 'This version has not been approved by a clinical reviewer.',
    });
  }

  const { rows: findings } = await query<{ n: number }>(
    `SELECT count(*)::int AS n FROM review_findings
      WHERE medication_id = $1 AND severity = 'high' AND status IN ('open','acknowledged')`,
    [version.medication_id],
    client,
  );
  if ((findings[0]?.n ?? 0) > 0) {
    blockers.push({
      code: 'open_high_findings',
      message: `${findings[0].n} unresolved high-severity review finding(s) must be closed first.`,
    });
  }

  // Claims that the workbook's audit flagged as uncited may not go live
  // without a source attached.
  const citations = await getCitations(versionId, client);
  const cited = new Set(citations.map((c) => c.fieldKey));
  for (const fieldKey of CITATION_REQUIRED_FIELDS) {
    const field = version.data[fieldKey];
    const hasContent =
      (field?.en?.state === 'provided' && field.en.text) ||
      (field?.he?.state === 'provided' && field.he.text);
    if (hasContent && !cited.has(fieldKey)) {
      blockers.push({
        code: 'missing_citation',
        message: `"${fieldKey}" carries a clinical claim with no source attached.`,
        fieldKey,
      });
    }
  }

  if (!version.reviewed_at) {
    blockers.push({ code: 'missing_review_date', message: 'A clinical review date is required.' });
  }
  return blockers;
}

export interface TransitionOptions {
  /**
   * Publish despite outstanding clinical gates. Only honoured while the
   * system-wide preview allowance is switched on, and always recorded.
   */
  acknowledgeUnvalidated?: boolean;
}

export async function transitionVersion(
  versionId: string,
  to: WorkflowState,
  actor: Actor,
  reason: string | null,
  options: TransitionOptions = {},
): Promise<VersionRow> {
  return withTransaction(async (db) => {
    const { rows: locked } = await query<VersionRow>(
      `SELECT v.*, m.slug FROM medication_versions v JOIN medications m ON m.id = v.medication_id
        WHERE v.id = $1 FOR UPDATE OF v`,
      [versionId],
      db,
    );
    const version = locked[0];
    if (!version) throw notFound('version_not_found', 'No such version.');

    const check = checkTransition(actor.role, version.state, to, reason);
    if (!check.ok) {
      if (check.error === 'forbidden') {
        throw forbidden('forbidden', 'Your role does not permit that transition.');
      }
      if (check.error === 'reason_required') {
        throw badRequest('reason_required', 'This transition requires a reason.');
      }
      throw conflict('invalid_transition', `Cannot move from "${version.state}" to "${to}".`);
    }

    let overridden: PublishBlocker[] = [];
    if (to === 'published') {
      const blockers = await publishBlockers(versionId, db);
      if (blockers.length > 0) {
        // The gates can be waived only deliberately: the allowance has to be
        // switched on for the whole system AND this call has to say so. The
        // record then carries the fact permanently.
        const allowed = await unvalidatedPublicationAllowed(db);
        if (!allowed || !options.acknowledgeUnvalidated) {
          throw conflict('publish_blocked', 'This version cannot be published yet.', {
            blockers,
            unvalidatedPublicationAllowed: allowed,
          });
        }
        overridden = blockers;
      }
      // Retire the version currently live, so exactly one stays published.
      const { rows: previous } = await query<{ id: string }>(
        `UPDATE medication_versions SET state = 'archived'
          WHERE medication_id = $1 AND state = 'published' RETURNING id`,
        [version.medication_id],
        db,
      );
      for (const prev of previous) await reindexState(prev.id, 'archived', db);
    }

    // Every value is bound, never interpolated, even though actor.id is a
    // UUID this server issued.
    const stamps: string[] = ['state = $2'];
    const params: unknown[] = [versionId, to];
    const bind = (value: unknown): string => {
      params.push(value);
      return `$${params.length}`;
    };
    // Bound on first use only: a transition that records no actor column must
    // not send a parameter its SQL never references.
    let actorRef: string | null = null;
    const actorParam = (): string => (actorRef ??= bind(actor.id));

    if (to === 'in_clinical_review') {
      stamps.push('submitted_at = now()', `submitted_by = ${actorParam()}`);
    }
    if (to === 'approved') {
      stamps.push(
        'approved_at = now()', `approved_by = ${actorParam()}`,
        `reviewed_by = ${actorParam()}`, 'review_decided_at = now()',
      );
    }
    if (to === 'changes_requested') {
      stamps.push(`reviewed_by = ${actorParam()}`, 'review_decided_at = now()');
    }
    if (to === 'published') {
      stamps.push('published_at = now()', `published_by = ${actorParam()}`);
      stamps.push(`published_unvalidated = ${bind(overridden.length > 0)}`);
      stamps.push(`overridden_blockers = ${bind(JSON.stringify(overridden))}`);
    }
    if (reason) stamps.push(`change_reason = ${bind(reason)}`);

    const { rows } = await query<VersionRow>(
      `UPDATE medication_versions SET ${stamps.join(', ')} WHERE id = $1 RETURNING *`,
      params,
      db,
    );

    await recordRevision(
      {
        medicationId: version.medication_id,
        versionId,
        actor,
        action: overridden.length > 0 ? 'published_unvalidated' : 'transitioned',
        fromState: version.state,
        toState: to,
        reason:
          overridden.length > 0
            ? `${reason ?? 'Published for evaluation before clinical review.'} ` +
              `Overridden: ${overridden.map((b) => b.code).join(', ')}.`
            : reason,
        fieldDiff: [],
      },
      db,
    );
    await reindexState(versionId, to, db, overridden.length > 0);
    return { ...rows[0], slug: version.slug };
  });
}

// --- Citations --------------------------------------------------------------

export interface CitationProvenance {
  /** Which external lookup the reviewer accepted this from, if any. */
  sourceProvider?: string | null;
  externalId?: string | null;
}

export async function addCitation(
  versionId: string,
  citation: Omit<Citation, 'id'>,
  actor: Actor,
  provenance: CitationProvenance = {},
): Promise<Citation> {
  const version = await getVersion(versionId);
  if (!version) throw notFound('version_not_found', 'No such version.');
  if (version.state === 'published' || version.state === 'archived') {
    throw conflict('not_editable', 'Citations are attached to an editable version, not a published one.');
  }
  const { rows } = await query<{ id: string }>(
    `INSERT INTO citations (version_id, field_key, title, document_ref, url, page,
                            jurisdiction, approval_status, reviewed_at, reviewed_by, created_by,
                            source_provider, external_id)
     VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$10,$11,$12) RETURNING id`,
    [
      versionId, citation.fieldKey, citation.title, citation.documentRef ?? null,
      citation.url ?? null, citation.page ?? null, citation.jurisdiction ?? null,
      citation.approvalStatus, citation.reviewedAt ?? null, actor.id,
      provenance.sourceProvider ?? null, provenance.externalId ?? null,
    ],
  );
  return { ...citation, id: rows[0].id };
}

export async function removeCitation(versionId: string, citationId: string): Promise<void> {
  const { rowCount } = await query(
    'DELETE FROM citations WHERE id = $1 AND version_id = $2',
    [citationId, versionId],
  );
  if (!rowCount) throw notFound('citation_not_found', 'No such citation.');
}

// --- Revisions --------------------------------------------------------------

interface RevisionInput {
  medicationId: string;
  versionId: string | null;
  actor: Actor;
  action: string;
  fromState?: WorkflowState | null;
  toState?: WorkflowState | null;
  reason?: string | null;
  fieldDiff: FieldChange[];
}

export async function recordRevision(input: RevisionInput, client?: Queryable): Promise<void> {
  await query(
    `INSERT INTO revisions
       (medication_id, version_id, actor_id, actor_email, action, from_state, to_state, reason, field_diff)
     VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)`,
    [
      input.medicationId, input.versionId, input.actor.id, input.actor.email, input.action,
      input.fromState ?? null, input.toState ?? null, input.reason ?? null,
      JSON.stringify(input.fieldDiff),
    ],
    client,
  );
}
