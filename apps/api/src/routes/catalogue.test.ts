import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { FastifyInstance } from 'fastify';
import { query } from '../db/pool.js';
import {
  closeTestApp, createTestApp, loginAs, loginAsAdmin, resetDatabase, seedUser, shutdown,
} from '../test/helpers.js';
import { SERTRALINE, medication } from '../test/fixtures.js';

let app: FastifyInstance;
let editorCookie: string;
let reviewerCookie: string;
let physicianCookie: string;
let adminCookie: string;

beforeAll(async () => {
  await resetDatabase();
  app = await createTestApp();
});
afterAll(async () => {
  await closeTestApp(app);
  await shutdown();
});

beforeEach(async () => {
  await resetDatabase();
  await seedUser({ email: 'editor@example.org', role: 'editor' });
  await seedUser({ email: 'reviewer@example.org', role: 'clinical_reviewer' });
  await seedUser({ email: 'doc@example.org', role: 'physician' });
  await seedUser({ email: 'admin@example.org', role: 'admin' });
  editorCookie = await loginAs(app, 'editor@example.org');
  reviewerCookie = await loginAs(app, 'reviewer@example.org');
  physicianCookie = await loginAs(app, 'doc@example.org');
  adminCookie = await loginAsAdmin(app, 'admin@example.org');
});

async function createDraft(data = SERTRALINE) {
  const res = await app.inject({
    method: 'POST', url: '/api/medications', headers: { cookie: editorCookie },
    payload: { data, sourceLabel: 'Test source', reviewedAt: '2026-01-15' },
  });
  expect(res.statusCode).toBe(200);
  return res.json().version as { id: string; slug: string; state: string };
}

async function transition(cookie: string, versionId: string, to: string, reason?: string) {
  return app.inject({
    method: 'POST', url: `/api/medications/versions/${versionId}/transition`,
    headers: { cookie }, payload: { to, reason },
  });
}

async function addCitation(versionId: string, fieldKey: string) {
  return app.inject({
    method: 'POST', url: `/api/medications/versions/${versionId}/citations`,
    headers: { cookie: editorCookie },
    payload: {
      fieldKey, title: 'Summary of Product Characteristics', page: '4.1',
      jurisdiction: 'IL', approvalStatus: 'approved', reviewedAt: '2026-01-10',
    },
  });
}

/** Drives a draft all the way to published. */
async function publishFully(data = SERTRALINE) {
  const version = await createDraft(data);
  for (const field of ['adult_indications', 'contraindications', 'qtc_adults', 'maximum_dose']) {
    if (data[field]) await addCitation(version.id, field);
  }
  await transition(editorCookie, version.id, 'in_clinical_review');
  await transition(reviewerCookie, version.id, 'approved');
  const published = await transition(adminCookie, version.id, 'published');
  return { version, published };
}

describe('draft creation and editing', () => {
  it('lets an editor create a draft, invisible to physicians', async () => {
    const version = await createDraft();
    expect(version.state).toBe('draft');
    expect(version.slug).toBe('sertraline');

    const asPhysician = await app.inject({
      method: 'GET', url: `/api/medications/${version.slug}`, headers: { cookie: physicianCookie },
    });
    expect(asPhysician.statusCode).toBe(404);
  });

  it('refuses a physician who asks for the draft view directly', async () => {
    const version = await createDraft();
    const res = await app.inject({
      method: 'GET', url: `/api/medications/${version.slug}?draft=true`,
      headers: { cookie: physicianCookie },
    });
    expect(res.statusCode).toBe(403);
  });

  it('does not let a physician create or edit anything', async () => {
    const create = await app.inject({
      method: 'POST', url: '/api/medications', headers: { cookie: physicianCookie },
      payload: { data: SERTRALINE },
    });
    expect(create.statusCode).toBe(403);
  });

  it('rejects an unknown field key rather than storing it', async () => {
    const res = await app.inject({
      method: 'POST', url: '/api/medications', headers: { cookie: editorCookie },
      payload: { data: { not_a_real_field: { en: { state: 'provided', text: 'x' }, he: { state: 'not_supplied', text: null } } } },
    });
    expect(res.statusCode).toBe(400);
  });

  it('stores clinical text exactly as supplied, without normalising units', async () => {
    const odd = medication({ generic_name: 'Testdrug', maximum_dose: '2mg/kg/day' });
    const version = await createDraft(odd);
    const res = await app.inject({
      method: 'GET', url: `/api/medications/${version.slug}?draft=true`,
      headers: { cookie: editorCookie },
    });
    expect(res.json().fields.maximum_dose.value.text).toBe('2mg/kg/day');
  });

  it('reports a missing field as not supplied rather than not applicable', async () => {
    const version = await createDraft(medication({ generic_name: 'Sparse' }));
    const res = await app.inject({
      method: 'GET', url: `/api/medications/${version.slug}?draft=true`,
      headers: { cookie: editorCookie },
    });
    expect(res.json().fields.monitoring_tests.value.state).toBe('not_supplied');
    expect(res.json().fields.monitoring_tests.value.state).not.toBe('not_applicable');
  });
});

describe('workflow transitions', () => {
  it('follows Draft -> Clinical Review -> Approved -> Published', async () => {
    const version = await createDraft();
    for (const f of ['adult_indications', 'contraindications', 'qtc_adults', 'maximum_dose']) {
      await addCitation(version.id, f);
    }
    expect((await transition(editorCookie, version.id, 'in_clinical_review')).statusCode).toBe(200);
    expect((await transition(reviewerCookie, version.id, 'approved')).statusCode).toBe(200);
    // Publishing is a separate, admin-held permission: clinical approval and
    // the act of going live are two different people.
    expect((await transition(adminCookie, version.id, 'published')).statusCode).toBe(200);

    const asPhysician = await app.inject({
      method: 'GET', url: `/api/medications/${version.slug}`, headers: { cookie: physicianCookie },
    });
    expect(asPhysician.statusCode).toBe(200);
    expect(asPhysician.json().state).toBe('published');
  });

  it('forbids an editor from approving their own work', async () => {
    const version = await createDraft();
    await transition(editorCookie, version.id, 'in_clinical_review');
    const res = await transition(editorCookie, version.id, 'approved');
    expect(res.statusCode).toBe(403);
  });

  it('forbids an editor from publishing', async () => {
    const version = await createDraft();
    for (const f of ['adult_indications', 'contraindications', 'qtc_adults', 'maximum_dose']) {
      await addCitation(version.id, f);
    }
    await transition(editorCookie, version.id, 'in_clinical_review');
    await transition(reviewerCookie, version.id, 'approved');
    const res = await transition(editorCookie, version.id, 'published');
    expect(res.statusCode).toBe(403);
  });

  it('rejects skipping review and publishing a draft directly', async () => {
    const version = await createDraft();
    const res = await transition(adminCookie, version.id, 'published');
    expect(res.statusCode).toBe(409);
    expect(res.json().error.code).toBe('invalid_transition');
  });

  it('requires a reason when requesting changes', async () => {
    const version = await createDraft();
    await transition(editorCookie, version.id, 'in_clinical_review');
    const without = await transition(reviewerCookie, version.id, 'changes_requested');
    expect(without.statusCode).toBe(400);
    expect(without.json().error.code).toBe('reason_required');

    const withReason = await transition(
      reviewerCookie, version.id, 'changes_requested', 'Dose range needs a source.',
    );
    expect(withReason.statusCode).toBe(200);
  });

  it('refuses to edit a version that is under review', async () => {
    const version = await createDraft();
    await transition(editorCookie, version.id, 'in_clinical_review');
    const res = await app.inject({
      method: 'PATCH', url: `/api/medications/versions/${version.id}`,
      headers: { cookie: editorCookie }, payload: { data: SERTRALINE },
    });
    expect(res.statusCode).toBe(409);
    expect(res.json().error.code).toBe('not_editable');
  });
});

describe('publication preconditions', () => {
  it('blocks publishing a clinical claim that has no citation', async () => {
    const version = await createDraft();
    await transition(editorCookie, version.id, 'in_clinical_review');
    await transition(reviewerCookie, version.id, 'approved');
    const res = await transition(adminCookie, version.id, 'published');
    expect(res.statusCode).toBe(409);
    expect(res.json().error.code).toBe('publish_blocked');
    const codes = res.json().error.details.blockers.map((b: { code: string }) => b.code);
    expect(codes).toContain('missing_citation');
  });

  it('blocks publishing while a high-severity finding is open', async () => {
    const version = await createDraft();
    for (const f of ['adult_indications', 'contraindications', 'qtc_adults', 'maximum_dose']) {
      await addCitation(version.id, f);
    }
    const { rows } = await query<{ id: string }>(
      'SELECT medication_id AS id FROM medication_versions WHERE id = $1', [version.id],
    );
    await query(
      `INSERT INTO review_findings (medication_id, severity, scope, issue_type, evidence, recommended_action)
       VALUES ($1, 'high', 'sertraline', 'Probable label inversion', 'Trade name looks like the generic name.',
               'Confirm against the authoritative Excel.')`,
      [rows[0].id],
    );
    await transition(editorCookie, version.id, 'in_clinical_review');
    await transition(reviewerCookie, version.id, 'approved');
    const res = await transition(adminCookie, version.id, 'published');
    expect(res.statusCode).toBe(409);
    const codes = res.json().error.details.blockers.map((b: { code: string }) => b.code);
    expect(codes).toContain('open_high_findings');
  });

  it('blocks publishing without a clinical review date', async () => {
    const res = await app.inject({
      method: 'POST', url: '/api/medications', headers: { cookie: editorCookie },
      payload: { data: SERTRALINE, sourceLabel: 'Test' },
    });
    const version = res.json().version;
    for (const f of ['adult_indications', 'contraindications', 'qtc_adults', 'maximum_dose']) {
      await addCitation(version.id, f);
    }
    await transition(editorCookie, version.id, 'in_clinical_review');
    await transition(reviewerCookie, version.id, 'approved');
    const publish = await transition(adminCookie, version.id, 'published');
    expect(publish.statusCode).toBe(409);
    const codes = publish.json().error.details.blockers.map((b: { code: string }) => b.code);
    expect(codes).toContain('missing_review_date');
  });

  it('lists every blocker at once', async () => {
    const version = await createDraft();
    const res = await app.inject({
      method: 'GET', url: `/api/medications/versions/${version.id}/workflow`,
      headers: { cookie: reviewerCookie },
    });
    expect(res.statusCode).toBe(200);
    const blockers = res.json().publishBlockers.map((b: { code: string }) => b.code);
    expect(blockers).toContain('not_clinically_approved');
    expect(blockers).toContain('missing_citation');
  });
});

describe('revision history', () => {
  it('records who changed what, when, and why', async () => {
    const version = await createDraft();
    const edited = { ...SERTRALINE, maximum_dose: { en: { state: 'provided' as const, text: '150 mg/day' }, he: { state: 'not_supplied' as const, text: null } } };
    await app.inject({
      method: 'PATCH', url: `/api/medications/versions/${version.id}`,
      headers: { cookie: editorCookie }, payload: { data: edited },
    });
    await transition(editorCookie, version.id, 'in_clinical_review');
    await transition(reviewerCookie, version.id, 'changes_requested', 'Confirm the maximum dose.');

    const res = await app.inject({
      method: 'GET', url: `/api/medications/${version.slug}/revisions`,
      headers: { cookie: reviewerCookie },
    });
    expect(res.statusCode).toBe(200);
    const revisions = res.json().revisions;
    const actions = revisions.map((r: { action: string }) => r.action);
    expect(actions).toContain('created');
    expect(actions).toContain('edited');
    expect(actions).toContain('transitioned');

    const edit = revisions.find((r: { action: string }) => r.action === 'edited');
    expect(edit.actor_email).toBe('editor@example.org');
    const doseChange = edit.field_diff.find((c: { fieldKey: string }) => c.fieldKey === 'maximum_dose');
    expect(doseChange.before.text).toBe('200 mg/day');
    expect(doseChange.after.text).toBe('150 mg/day');

    const rejection = revisions.find(
      (r: { to_state: string }) => r.to_state === 'changes_requested',
    );
    expect(rejection.reason).toBe('Confirm the maximum dose.');
    expect(rejection.actor_email).toBe('reviewer@example.org');
  });

  it('cannot be rewritten', async () => {
    await createDraft();
    await expect(query(`UPDATE revisions SET reason = 'rewritten'`)).rejects.toThrow();
    await expect(query(`DELETE FROM revisions`)).rejects.toThrow();
  });

  it('is hidden from physicians', async () => {
    const version = await createDraft();
    const res = await app.inject({
      method: 'GET', url: `/api/medications/${version.slug}/revisions`,
      headers: { cookie: physicianCookie },
    });
    expect(res.statusCode).toBe(403);
  });
});

describe('editing published content', () => {
  it('opens a new version instead of changing the live one', async () => {
    const { version } = await publishFully();
    const draft = await app.inject({
      method: 'POST', url: `/api/medications/${version.slug}/draft`,
      headers: { cookie: editorCookie },
    });
    expect(draft.statusCode).toBe(200);
    const newVersionId = draft.json().version.id;
    expect(newVersionId).not.toBe(version.id);

    // The published version is untouched and still what physicians see.
    const live = await app.inject({
      method: 'GET', url: `/api/medications/${version.slug}`, headers: { cookie: physicianCookie },
    });
    expect(live.json().versionId).toBe(version.id);
    expect(live.json().state).toBe('published');
  });

  it('keeps exactly one published version after republishing', async () => {
    const { version } = await publishFully();
    const draft = await app.inject({
      method: 'POST', url: `/api/medications/${version.slug}/draft`,
      headers: { cookie: editorCookie },
    });
    const v2 = draft.json().version.id;
    await transition(editorCookie, v2, 'in_clinical_review');
    await transition(reviewerCookie, v2, 'approved');
    const published = await transition(adminCookie, v2, 'published');
    expect(published.statusCode).toBe(200);

    const { rows } = await query<{ n: number }>(
      `SELECT count(*)::int AS n FROM medication_versions WHERE state = 'published'`,
    );
    expect(rows[0].n).toBe(1);
    const live = await app.inject({
      method: 'GET', url: `/api/medications/${version.slug}`, headers: { cookie: physicianCookie },
    });
    expect(live.json().versionId).toBe(v2);
  });
});

describe('preview publication', () => {
  async function allowUnvalidated(allowed: boolean) {
    await query(
      `UPDATE settings SET value = $1::jsonb WHERE key = 'publication.allow_unvalidated'`,
      [JSON.stringify(allowed)],
    );
  }

  it('is refused while the allowance is off, even when acknowledged', async () => {
    await allowUnvalidated(false);
    const version = await createDraft();
    await transition(editorCookie, version.id, 'in_clinical_review');
    await transition(reviewerCookie, version.id, 'approved');

    const res = await app.inject({
      method: 'POST', url: `/api/medications/versions/${version.id}/transition`,
      headers: { cookie: adminCookie },
      payload: { to: 'published', acknowledgeUnvalidated: true },
    });
    expect(res.statusCode).toBe(409);
    expect(res.json().error.code).toBe('publish_blocked');
    expect(res.json().error.details.unvalidatedPublicationAllowed).toBe(false);
  });

  it('is refused while the allowance is on but not acknowledged', async () => {
    await allowUnvalidated(true);
    const version = await createDraft();
    await transition(editorCookie, version.id, 'in_clinical_review');
    await transition(reviewerCookie, version.id, 'approved');

    const res = await transition(adminCookie, version.id, 'published');
    expect(res.statusCode).toBe(409);
    expect(res.json().error.details.unvalidatedPublicationAllowed).toBe(true);
  });

  it('publishes when both the allowance and the acknowledgement are present', async () => {
    await allowUnvalidated(true);
    const version = await createDraft();
    await transition(editorCookie, version.id, 'in_clinical_review');
    await transition(reviewerCookie, version.id, 'approved');

    const res = await app.inject({
      method: 'POST', url: `/api/medications/versions/${version.id}/transition`,
      headers: { cookie: adminCookie },
      payload: { to: 'published', acknowledgeUnvalidated: true },
    });
    expect(res.statusCode).toBe(200);
    expect(res.json().version.publishedUnvalidated).toBe(true);
    expect(res.json().version.overriddenBlockers.length).toBeGreaterThan(0);
  });

  it('records permanently on the record what was overridden', async () => {
    await allowUnvalidated(true);
    const version = await createDraft();
    await transition(editorCookie, version.id, 'in_clinical_review');
    await transition(reviewerCookie, version.id, 'approved');
    await app.inject({
      method: 'POST', url: `/api/medications/versions/${version.id}/transition`,
      headers: { cookie: adminCookie },
      payload: { to: 'published', acknowledgeUnvalidated: true },
    });

    const shown = await app.inject({
      method: 'GET', url: `/api/medications/${version.slug}`, headers: { cookie: physicianCookie },
    });
    expect(shown.statusCode).toBe(200);
    expect(shown.json().publishedUnvalidated).toBe(true);
    const codes = shown.json().overriddenBlockers.map((b: { code: string }) => b.code);
    expect(codes).toContain('missing_citation');

    // And in the revision trail, which cannot be rewritten.
    const revisions = await app.inject({
      method: 'GET', url: `/api/medications/${version.slug}/revisions`,
      headers: { cookie: reviewerCookie },
    });
    const entry = revisions.json().revisions.find(
      (r: { action: string }) => r.action === 'published_unvalidated',
    );
    expect(entry).toBeDefined();
    expect(entry.reason).toMatch(/Overridden:/);
  });

  it('flags the record in search results so it cannot be mistaken for reviewed', async () => {
    await allowUnvalidated(true);
    const version = await createDraft();
    await transition(editorCookie, version.id, 'in_clinical_review');
    await transition(reviewerCookie, version.id, 'approved');
    await app.inject({
      method: 'POST', url: `/api/medications/versions/${version.id}/transition`,
      headers: { cookie: adminCookie },
      payload: { to: 'published', acknowledgeUnvalidated: true },
    });

    const res = await app.inject({
      method: 'GET', url: '/api/search?q=sertraline', headers: { cookie: physicianCookie },
    });
    expect(res.json().hits[0].publishedUnvalidated).toBe(true);
  });

  it('does not flag a record that passed every gate', async () => {
    await allowUnvalidated(true);
    const { version } = await publishFully();
    const shown = await app.inject({
      method: 'GET', url: `/api/medications/${version.slug}`, headers: { cookie: physicianCookie },
    });
    expect(shown.json().publishedUnvalidated).toBe(false);
    expect(shown.json().overriddenBlockers).toEqual([]);
  });

  it('tells every client that the catalogue is in evaluation', async () => {
    await allowUnvalidated(true);
    const res = await app.inject({ method: 'GET', url: '/api/settings/public' });
    expect(res.json().evaluationMode).toBe(true);
    expect(res.json().evaluationNotice).toMatch(/not use it for clinical decisions/i);

    await allowUnvalidated(false);
    const off = await app.inject({ method: 'GET', url: '/api/settings/public' });
    expect(off.json().evaluationMode).toBe(false);
    expect(off.json().evaluationNotice).toBeNull();
  });

  it('only an administrator can switch the allowance on', async () => {
    const denied = await app.inject({
      method: 'PUT', url: '/api/settings/publication.allow_unvalidated',
      headers: { cookie: editorCookie }, payload: { value: true },
    });
    expect(denied.statusCode).toBe(403);

    const allowed = await app.inject({
      method: 'PUT', url: '/api/settings/publication.allow_unvalidated',
      headers: { cookie: adminCookie }, payload: { value: true },
    });
    expect(allowed.statusCode).toBe(200);
  });
});

describe('comparison', () => {
  it('compares up to three published medications and refuses a fourth', async () => {
    await publishFully();
    await publishFully(medication({
      generic_name: 'Fluoxetine', trade_names: 'Prozac', therapeutic_group: 'Antidepressants',
      drug_class: 'SSRI', maximum_dose: '80 mg/day', adult_indications: 'MDD',
      contraindications: 'MAOI', qtc_adults: 'Minimal',
    }));

    const ok = await app.inject({
      method: 'POST', url: '/api/medications/compare', headers: { cookie: physicianCookie },
      payload: { slugs: ['sertraline', 'fluoxetine'] },
    });
    expect(ok.statusCode).toBe(200);
    expect(ok.json().medications).toHaveLength(2);

    const tooMany = await app.inject({
      method: 'POST', url: '/api/medications/compare', headers: { cookie: physicianCookie },
      payload: { slugs: ['sertraline', 'fluoxetine', 'sertraline', 'fluoxetine'] },
    });
    expect(tooMany.statusCode).toBe(400);
  });
});
