/**
 * Seeds the catalogue from the website-snapshot workbook.
 *
 * Every record is marked "Unvalidated website snapshot" and left in DRAFT.
 * Nothing is published: the snapshot is a starting point for review against
 * the authoritative Excel, not clinical content.
 */
import { readFile } from 'node:fs/promises';
import { basename, resolve } from 'node:path';
import { pool, query } from '../db/pool.js';
import { hashPassword } from '../lib/crypto.js';
import { uploadWorkbook, validateBatch, commitBatch } from '../services/import/pipeline.js';
import { suggestMapping } from '../services/import/mapping.js';
import type { Actor } from '../services/catalogue.js';

const SNAPSHOT_STATUS = 'Unvalidated website snapshot';

async function ensureSeedUser(): Promise<Actor> {
  const email = process.env.SEED_ADMIN_EMAIL ?? 'seed@localhost';
  const { rows: existing } = await query<{ id: string; email: string }>(
    'SELECT id, email FROM users WHERE email_normalized = $1',
    [email.toLowerCase()],
  );
  if (existing[0]) return { ...existing[0], role: 'admin' as never };

  // A locked account used only to attribute the seed import. It has no usable
  // password and cannot sign in.
  const { rows } = await query<{ id: string; email: string }>(
    `INSERT INTO users (email, email_normalized, display_name, role, status, password_hash)
     VALUES ($1, $2, 'Seed import', 'admin', 'deactivated', $3) RETURNING id, email`,
    [email, email.toLowerCase(), await hashPassword(crypto.randomUUID() + crypto.randomUUID())],
  );
  return { ...rows[0], role: 'admin' as never };
}

async function main(): Promise<void> {
  const path = process.argv[2];
  if (!path) {
    console.error('Usage: pnpm seed <path-to-workbook.xlsx>');
    process.exit(1);
  }

  const actor = await ensureSeedUser();
  const buffer = await readFile(resolve(path));

  const { rows: already } = await query<{ n: number }>('SELECT count(*)::int AS n FROM medications');
  if (already[0].n > 0) {
    console.error(`Catalogue already holds ${already[0].n} medication(s); refusing to seed over it.`);
    console.error('Use the import screens to load another workbook.');
    process.exit(1);
  }

  console.log('Uploading workbook…');
  const uploaded = await uploadWorkbook({ filename: basename(path), buffer }, actor);
  if (!uploaded.suggestedSheet) throw new Error('No sheet in this workbook looks like a catalogue.');
  console.log(`  sheet: ${uploaded.suggestedSheet}`);

  const mapping: Record<string, string | null> = {};
  for (const suggestion of suggestMapping(
    uploaded.sheets.find((s) => s.name === uploaded.suggestedSheet)!.headers,
  )) {
    mapping[suggestion.header] = suggestion.fieldKey;
  }
  const mappedCount = Object.values(mapping).filter(Boolean).length;
  console.log(`  mapped ${mappedCount} columns`);

  console.log('Validating…');
  const validated = await validateBatch(uploaded.batchId, {
    sheetName: uploaded.suggestedSheet,
    mapping,
    locale: 'en',
    sourceLabel: SNAPSHOT_STATUS,
  });
  console.log(
    `  ${validated.stats.totalRows} rows: ${validated.stats.create} new, ` +
      `${validated.stats.update} updates, ${validated.stats.duplicate} duplicates, ` +
      `${validated.stats.error} errors`,
  );
  console.log(
    `  findings: ${validated.stats.findings.high} high, ${validated.stats.findings.medium} medium, ` +
      `${validated.stats.findings.low} low`,
  );

  console.log('Committing as drafts…');
  const committed = await commitBatch(uploaded.batchId, actor, {
    validationStatus: SNAPSHOT_STATUS,
    sourceDocument: basename(path),
  });
  console.log(`  created ${committed.created}, updated ${committed.updated}`);

  console.log('');
  console.log('Seed complete. Every record is a DRAFT marked');
  console.log(`  "${SNAPSHOT_STATUS}"`);
  console.log('and is not visible to physicians until it has been reviewed against the');
  console.log('authoritative Excel, cited, clinically approved and published.');
}

main()
  .then(() => pool.end())
  .then(() => process.exit(0))
  .catch((err) => {
    console.error(err instanceof Error ? err.message : err);
    process.exit(1);
  });
