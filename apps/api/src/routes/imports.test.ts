import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { FastifyInstance } from 'fastify';
import { query } from '../db/pool.js';
import {
  closeTestApp, createTestApp, loginAs, loginAsAdmin, resetDatabase, seedUser, shutdown,
} from '../test/helpers.js';

const WORKBOOK = join(
  dirname(fileURLToPath(import.meta.url)),
  '../test/data/medication_catalogue_central_export.xlsx',
);

let app: FastifyInstance;
let editorCookie: string;
let adminCookie: string;
let physicianCookie: string;

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
  await seedUser({ email: 'admin@example.org', role: 'admin' });
  await seedUser({ email: 'doc@example.org', role: 'physician' });
  editorCookie = await loginAs(app, 'editor@example.org');
  adminCookie = await loginAsAdmin(app, 'admin@example.org');
  physicianCookie = await loginAs(app, 'doc@example.org');
});

/** Builds a multipart body for the upload endpoint. */
function multipart(filename: string, buffer: Buffer) {
  const boundary = '----medcatTestBoundary';
  const head = Buffer.from(
    `--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="${filename}"\r\n` +
      'Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n',
  );
  const tail = Buffer.from(`\r\n--${boundary}--\r\n`);
  return {
    payload: Buffer.concat([head, buffer, tail]),
    headers: { 'content-type': `multipart/form-data; boundary=${boundary}` },
  };
}

async function upload(cookie = editorCookie, filename = 'catalogue.xlsx') {
  const buffer = readFileSync(WORKBOOK);
  const mp = multipart(filename, buffer);
  return app.inject({
    method: 'POST', url: '/api/imports',
    headers: { ...mp.headers, cookie }, payload: mp.payload,
  });
}

async function uploadAndValidate() {
  const uploaded = await upload();
  expect(uploaded.statusCode).toBe(200);
  const { batchId, suggestedSheet, suggestedMapping } = uploaded.json();

  const mapping: Record<string, string | null> = {};
  for (const m of suggestedMapping) mapping[m.header] = m.fieldKey;

  const validated = await app.inject({
    method: 'POST', url: `/api/imports/${batchId}/validate`, headers: { cookie: editorCookie },
    payload: { sheetName: suggestedSheet, mapping, locale: 'en', sourceLabel: 'Website snapshot 2026-09' },
  });
  expect(validated.statusCode).toBe(200);
  return { batchId, suggestedSheet, mapping, result: validated.json() };
}

describe('permissions', () => {
  it('does not let a physician upload a workbook', async () => {
    const res = await upload(physicianCookie);
    expect(res.statusCode).toBe(403);
  });

  it('does not let an editor commit an import', async () => {
    const { batchId, result } = await uploadAndValidate();
    expect(result.stats.findings.high).toBeGreaterThan(0);
    const res = await app.inject({
      method: 'POST', url: `/api/imports/${batchId}/commit`,
      headers: { cookie: editorCookie }, payload: { acknowledgeFindings: true },
    });
    expect(res.statusCode).toBe(403);
  });
});

describe('reading the supplied workbook', () => {
  it('finds the catalogue sheet and maps its columns', async () => {
    const res = await upload();
    expect(res.statusCode).toBe(200);
    const body = res.json();
    expect(body.sheets.map((s: { name: string }) => s.name)).toContain('Medication Catalogue');
    expect(body.suggestedSheet).toBe('Medication Catalogue');

    const mapped = Object.fromEntries(
      body.suggestedMapping
        .filter((m: { fieldKey: string | null }) => m.fieldKey)
        .map((m: { header: string; fieldKey: string }) => [m.header, m.fieldKey]),
    );
    expect(mapped['Medication']).toBe('generic_name');
    expect(mapped['Trade Names']).toBe('trade_names');
    expect(mapped['Therapeutic Group']).toBe('therapeutic_group');
    expect(mapped['Category / Class']).toBe('drug_class');
    expect(mapped['Mechanism']).toBe('mechanism');
    expect(mapped['Maximum Dose']).toBe('maximum_dose');
    expect(mapped['Contraindications']).toBe('contraindications');
  });

  it('rejects a second upload of the same workbook', async () => {
    await upload();
    const again = await upload();
    expect(again.statusCode).toBe(409);
    expect(again.json().error.code).toBe('duplicate_upload');
  });

  it('rejects a non-spreadsheet file', async () => {
    const mp = multipart('notes.txt', Buffer.from('hello'));
    const res = await app.inject({
      method: 'POST', url: '/api/imports',
      headers: { ...mp.headers, cookie: editorCookie }, payload: mp.payload,
    });
    expect(res.statusCode).toBe(400);
    expect(res.json().error.code).toBe('unsupported_file');
  });
});

describe('validation preview', () => {
  it('previews all 44 rows as new without touching the catalogue', async () => {
    const { result } = await uploadAndValidate();
    expect(result.stats.totalRows).toBe(44);
    expect(result.stats.create).toBe(44);
    expect(result.stats.update).toBe(0);

    const { rows } = await query<{ n: number }>('SELECT count(*)::int AS n FROM medications');
    expect(rows[0].n).toBe(0);
  });

  it('records blank cells as not supplied, never as not applicable', async () => {
    const { batchId } = await uploadAndValidate();
    const { rows } = await query<{ mapped: Record<string, Record<string, { state: string }>> }>(
      `SELECT mapped FROM import_rows WHERE batch_id = $1 AND slug = 'methylphenidate'`,
      [batchId],
    );
    expect(rows[0].mapped.monitoring_tests.en.state).toBe('not_supplied');
    const states = Object.values(rows[0].mapped).map((f) => f.en.state);
    expect(states).not.toContain('not_applicable');
  });

  it('keeps clinical text verbatim, including odd unit spacing', async () => {
    const { batchId } = await uploadAndValidate();
    const { rows } = await query<{ mapped: Record<string, Record<string, { text: string }>> }>(
      `SELECT mapped FROM import_rows WHERE batch_id = $1 AND slug = 'methylphenidate'`,
      [batchId],
    );
    expect(rows[0].mapped.maximum_dose.en.text).toBe('2mg/kg/day');
  });
});

describe('data quality detectors', () => {
  it('flags the NRI / Atomoxetine label inversion', async () => {
    const { result } = await uploadAndValidate();
    const finding = result.findings.find(
      (f: { detector: string }) => f.detector === 'suspected_label_inversion',
    );
    expect(finding).toBeDefined();
    expect(finding.severity).toBe('high');
    expect(finding.evidence).toContain('NRI');
    expect(finding.recommendedAction).toMatch(/do not auto-correct/i);
  });

  it('flags the clinical note whose threshold has no number', async () => {
    const { result } = await uploadAndValidate();
    const findings = result.findings.filter(
      (f: { detector: string }) => f.detector === 'incomplete_threshold',
    );
    expect(findings.length).toBeGreaterThan(0);
    expect(findings.some((f: { evidence: string }) => /29 gimel/i.test(f.evidence))).toBe(true);
  });

  it('flags uncited clinical claims', async () => {
    const { result } = await uploadAndValidate();
    const uncited = result.findings.filter((f: { detector: string }) => f.detector === 'uncited_claim');
    expect(uncited.length).toBeGreaterThan(0);
    expect(uncited[0].severity).toBe('high');
  });

  it('flags "Blood pressure" listed as a side effect with no direction', async () => {
    const { result } = await uploadAndValidate();
    const ambiguous = result.findings.filter(
      (f: { detector: string }) => f.detector === 'ambiguous_side_effect',
    );
    expect(ambiguous.length).toBeGreaterThan(0);
    expect(ambiguous[0].evidence.toLowerCase()).toContain('blood pressure');
  });

  it('flags class-level text repeated across stimulant rows', async () => {
    const { result } = await uploadAndValidate();
    const repeated = result.findings.filter(
      (f: { detector: string }) => f.detector === 'repeated_class_text',
    );
    expect(repeated.length).toBeGreaterThan(0);
  });

  it('flags fields that are blank across most of the workbook', async () => {
    const { result } = await uploadAndValidate();
    const missingness = result.findings.filter(
      (f: { detector: string }) => f.detector === 'high_missingness',
    );
    const fields = missingness.map((f: { fieldKey: string }) => f.fieldKey);
    expect(fields).toContain('starting_age');
    expect(fields).toContain('monitoring_tests');
  });

  it('surfaces findings through the review report', async () => {
    const { batchId } = await uploadAndValidate();
    const res = await app.inject({
      method: 'GET', url: `/api/review/findings?batchId=${batchId}&severity=high`,
      headers: { cookie: editorCookie },
    });
    expect(res.statusCode).toBe(200);
    expect(res.json().findings.length).toBeGreaterThan(0);
    expect(res.json().findings.every((f: { severity: string }) => f.severity === 'high')).toBe(true);
  });
});

describe('committing an import', () => {
  it('refuses to commit while high-severity findings are unacknowledged', async () => {
    const { batchId } = await uploadAndValidate();
    const res = await app.inject({
      method: 'POST', url: `/api/imports/${batchId}/commit`,
      headers: { cookie: adminCookie }, payload: {},
    });
    expect(res.statusCode).toBe(400);
    expect(res.json().error.code).toBe('unacknowledged_findings');
  });

  it('stages every row as a draft and shows nothing to physicians', async () => {
    const { batchId } = await uploadAndValidate();
    const res = await app.inject({
      method: 'POST', url: `/api/imports/${batchId}/commit`,
      headers: { cookie: adminCookie }, payload: { acknowledgeFindings: true },
    });
    expect(res.statusCode).toBe(200);
    expect(res.json().created).toBe(44);

    const { rows: states } = await query<{ state: string; n: number }>(
      `SELECT state::text AS state, count(*)::int AS n FROM medication_versions GROUP BY state`,
    );
    expect(states).toEqual([{ state: 'draft', n: 44 }]);

    // A physician searching or fetching sees nothing at all.
    const fetch = await app.inject({
      method: 'GET', url: '/api/medications/sertraline', headers: { cookie: physicianCookie },
    });
    expect(fetch.statusCode).toBe(404);
  });

  it('marks imported records as unvalidated until reviewed', async () => {
    const { batchId } = await uploadAndValidate();
    await app.inject({
      method: 'POST', url: `/api/imports/${batchId}/commit`,
      headers: { cookie: adminCookie },
      payload: { acknowledgeFindings: true, validationStatus: 'Unvalidated website snapshot' },
    });
    const { rows } = await query<{ validation_status: string }>(
      `SELECT DISTINCT validation_status FROM medication_versions`,
    );
    expect(rows).toEqual([{ validation_status: 'Unvalidated website snapshot' }]);
  });

  it('keeps the source workbook and links every record to its batch', async () => {
    const { batchId } = await uploadAndValidate();
    await app.inject({
      method: 'POST', url: `/api/imports/${batchId}/commit`,
      headers: { cookie: adminCookie }, payload: { acknowledgeFindings: true },
    });
    const { rows } = await query<{ n: number }>(
      'SELECT count(*)::int AS n FROM medication_versions WHERE import_batch_id = $1', [batchId],
    );
    expect(rows[0].n).toBe(44);

    const batch = await app.inject({
      method: 'GET', url: `/api/imports/${batchId}`, headers: { cookie: editorCookie },
    });
    expect(batch.json().batch.sha256).toMatch(/^[0-9a-f]{64}$/);
    expect(batch.json().batch.status).toBe('committed');
  });

  it('refuses to commit the same batch twice', async () => {
    const { batchId } = await uploadAndValidate();
    await app.inject({
      method: 'POST', url: `/api/imports/${batchId}/commit`,
      headers: { cookie: adminCookie }, payload: { acknowledgeFindings: true },
    });
    const again = await app.inject({
      method: 'POST', url: `/api/imports/${batchId}/commit`,
      headers: { cookie: adminCookie }, payload: { acknowledgeFindings: true },
    });
    expect(again.statusCode).toBe(409);
    expect(again.json().error.code).toBe('already_committed');
  });

  it('attaches findings to the records they concern so they block publication', async () => {
    const { batchId } = await uploadAndValidate();
    await app.inject({
      method: 'POST', url: `/api/imports/${batchId}/commit`,
      headers: { cookie: adminCookie }, payload: { acknowledgeFindings: true },
    });
    const { rows } = await query<{ n: number }>(
      `SELECT count(*)::int AS n FROM review_findings WHERE medication_id IS NOT NULL`,
    );
    expect(rows[0].n).toBeGreaterThan(0);
  });
});
