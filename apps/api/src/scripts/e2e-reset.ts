/**
 * Prepares the end-to-end database.
 *
 * Runs migrations, wipes existing data, imports the website snapshot as
 * drafts, publishes a small set of records through the real workflow, and
 * creates one account per role. Everything here goes through the same service
 * layer the application uses, so the fixture cannot drift from real behaviour.
 */
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { CITATION_REQUIRED_FIELDS, type Role } from '@med/shared';
import { pool, query } from '../db/pool.js';
import { up as migrateUp } from '../db/migrate.js';
import { hashPassword } from '../lib/crypto.js';
import { addCitation, transitionVersion, type Actor } from '../services/catalogue.js';
import { commitBatch, uploadWorkbook, validateBatch } from '../services/import/pipeline.js';
import { suggestMapping } from '../services/import/mapping.js';
import { ensureDefaultSettings } from '../services/settings.js';

export const E2E_PASSWORD = 'e2e-test-password-value-1';
const PUBLISHED = ['sertraline', 'fluoxetine', 'methylphenidate', 'escitalopram', 'risperidone'];
const APPEND_ONLY = ['audit_log', 'revisions'];

async function wipe(): Promise<void> {
  const { rows } = await query<{ tablename: string }>(
    `SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'schema_migrations'`,
  );
  for (const t of APPEND_ONLY) {
    await query(`GRANT TRUNCATE ON TABLE ${t} TO CURRENT_USER`);
    await query(`ALTER TABLE ${t} DISABLE TRIGGER USER`);
  }
  await query(`TRUNCATE ${rows.map((r) => `"${r.tablename}"`).join(', ')} RESTART IDENTITY CASCADE`);
  for (const t of APPEND_ONLY) {
    await query(`ALTER TABLE ${t} ENABLE TRIGGER USER`);
    await query(`REVOKE TRUNCATE ON TABLE ${t} FROM CURRENT_USER`);
  }
  await ensureDefaultSettings();
}

async function makeUser(email: string, role: Role, name: string): Promise<Actor> {
  const { rows } = await query<{ id: string; email: string }>(
    `INSERT INTO users (email, email_normalized, display_name, role, status, password_hash,
                        password_changed_at, email_verified_at)
     VALUES ($1, $2, $3, $4, 'active', $5, now(), now()) RETURNING id, email`,
    [email, email.toLowerCase(), name, role, await hashPassword(E2E_PASSWORD)],
  );
  return { ...rows[0], role };
}

async function main(): Promise<void> {
  await migrateUp(() => {});
  await wipe();

  const editor = await makeUser('editor@example.org', 'editor', 'Test Editor');
  const reviewer = await makeUser('reviewer@example.org', 'clinical_reviewer', 'Test Reviewer');
  const admin = await makeUser('admin@example.org', 'admin', 'Test Admin');
  await makeUser('physician@example.org', 'physician', 'Test Physician');
  // Used only by the sign-in journey tests, so that first-time MFA enrolment
  // can be exercised from a clean state. The setup project never touches it.
  await makeUser('mfa-admin@example.org', 'admin', 'Test MFA Admin');

  const path = resolve(import.meta.dirname, '../test/data/medication_catalogue_central_export.xlsx');
  const buffer = await readFile(path);
  const uploaded = await uploadWorkbook({ filename: 'catalogue.xlsx', buffer }, admin);
  const sheet = uploaded.sheets.find((s) => s.name === uploaded.suggestedSheet)!;
  const mapping: Record<string, string | null> = {};
  for (const suggestion of suggestMapping(sheet.headers)) mapping[suggestion.header] = suggestion.fieldKey;

  await validateBatch(uploaded.batchId, {
    sheetName: sheet.name, mapping, locale: 'en',
    sourceLabel: 'Unvalidated website snapshot',
  });
  await commitBatch(uploaded.batchId, admin, { validationStatus: 'Unvalidated website snapshot' });

  for (const slug of PUBLISHED) {
    const { rows } = await query<{ id: string; medication_id: string; data: Record<string, { en?: { state: string } }> }>(
      `SELECT v.id, v.medication_id, v.data FROM medication_versions v
         JOIN medications m ON m.id = v.medication_id
        WHERE m.slug = $1 AND v.state = 'draft'`,
      [slug],
    );
    if (!rows[0]) continue;
    const version = rows[0];

    for (const fieldKey of CITATION_REQUIRED_FIELDS) {
      if (version.data[fieldKey]?.en?.state !== 'provided') continue;
      await addCitation(
        version.id,
        {
          fieldKey, title: 'Summary of Product Characteristics', documentRef: 'SmPC',
          url: null, page: '4.1', jurisdiction: 'IL', approvalStatus: 'approved',
          reviewedAt: '2026-01-01',
        },
        editor,
      );
    }
    await query('UPDATE medication_versions SET reviewed_at = $2 WHERE id = $1', [version.id, '2026-01-01']);
    await query(
      `UPDATE review_findings SET status = 'resolved', resolution_note = 'Reviewed for test fixture',
              resolved_by = $2, resolved_at = now()
        WHERE medication_id = $1 AND severity = 'high'`,
      [version.medication_id, reviewer.id],
    );

    await transitionVersion(version.id, 'in_clinical_review', editor, null);
    await transitionVersion(version.id, 'approved', reviewer, null);
    await transitionVersion(version.id, 'published', admin, null);
  }

  // A trade-name alias, so alias search has something to find.
  await query(
    `INSERT INTO medication_aliases (medication_id, alias, alias_normalized, kind)
     SELECT id, 'Zoloft', 'zoloft', 'trade_name' FROM medications WHERE slug = 'sertraline'
     ON CONFLICT DO NOTHING`,
  );

  console.log('e2e database ready');
}

main().then(() => pool.end()).then(() => process.exit(0)).catch((err) => {
  console.error(err);
  process.exit(1);
});
