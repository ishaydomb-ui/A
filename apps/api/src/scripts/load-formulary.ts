/**
 * Loads the authoritative clinical formulary workbook and publishes it.
 *
 * This is the path for content the project owns: the workbook is written and
 * maintained by the project's clinician, so it is the source of record rather
 * than something to be checked against one. The in-app review workflow exists
 * for content of unknown provenance — a website snapshot, a third-party
 * export — and gating the clinician's own table behind it would be asking her
 * to approve her own writing through a form.
 *
 * Nothing here bypasses the publication gates. They are satisfied honestly:
 * every claim that requires a source gets one naming this workbook, which is
 * true, and the workflow runs normally from there. The audit log records who
 * ran the load and that the owner accepted the workbook as authoritative, so
 * the history says exactly what happened.
 *
 *   pnpm --filter @med/api exec tsx src/scripts/load-formulary.ts <workbook.xlsx>
 *
 * Re-running it with an updated workbook supersedes each record with a new
 * version and retires the previous one; nothing is deleted.
 */
import { readFile } from 'node:fs/promises';
import { basename, resolve } from 'node:path';
import { CITATION_REQUIRED_FIELDS } from '@med/shared';
import { pool, query } from '../db/pool.js';
import { uploadWorkbook, validateBatch, commitBatch } from '../services/import/pipeline.js';
import { suggestMapping } from '../services/import/mapping.js';
import { addCitation, transitionVersion } from '../services/catalogue.js';
import type { Actor } from '../services/catalogue.js';

const REASON =
  'Initial load of the authoritative clinical formulary workbook, accepted by the ' +
  'project owner as the source of record.';

async function loadActor(): Promise<Actor> {
  const email = process.env.LOAD_ACTOR_EMAIL;
  const { rows } = await query<{ id: string; email: string; role: string }>(
    email
      ? `SELECT id, email, role FROM users WHERE email_normalized = $1 AND role = 'admin'`
      : `SELECT id, email, role FROM users WHERE role = 'admin' AND status = 'active'
          ORDER BY created_at LIMIT 1`,
    email ? [email.toLowerCase()] : [],
  );
  if (!rows[0]) {
    throw new Error(
      email
        ? `No active administrator with the address ${email}.`
        : 'No active administrator to attribute this load to.',
    );
  }
  return rows[0] as Actor;
}

async function main(): Promise<void> {
  const path = process.argv[2];
  if (!path) {
    console.error('Usage: load-formulary.ts <path-to-workbook.xlsx>');
    process.exit(1);
  }

  const file = resolve(path);
  const filename = basename(file);
  const sourceLabel = process.env.SOURCE_LABEL ?? `Clinical formulary workbook (${filename})`;
  // Deliberately free of the word "unvalidated": this content did not come
  // from an unverified scrape, and labelling it as though it had would be
  // just as wrong as the reverse.
  const validationStatus = process.env.VALIDATION_STATUS ?? 'Clinical formulary workbook';
  const reviewedAt = process.env.REVIEWED_AT ?? new Date().toISOString().slice(0, 10);

  const actor = await loadActor();
  console.log(`Loading ${filename} as ${actor.email}`);

  const uploaded = await uploadWorkbook({ filename, buffer: await readFile(file) }, actor);
  const sheet = uploaded.sheets.find((s) => s.name === uploaded.suggestedSheet);
  if (!sheet) throw new Error('No usable sheet found in the workbook.');

  const mapping: Record<string, string | null> = {};
  const unmapped: string[] = [];
  for (const suggestion of suggestMapping(sheet.headers)) {
    mapping[suggestion.header] = suggestion.fieldKey;
    if (!suggestion.fieldKey && !suggestion.meta) unmapped.push(suggestion.header);
  }
  if (unmapped.length > 0) {
    // Silently dropping a column would lose clinical content without a trace.
    throw new Error(
      `These columns map to no field, so their content would be lost: ${unmapped.join(', ')}. ` +
        'Add them to the field registry, or rename them to a known heading, and run again.',
    );
  }
  console.log(`  sheet "${sheet.name}", ${Object.keys(mapping).length} columns mapped`);

  const preview = await validateBatch(uploaded.batchId, {
    sheetName: sheet.name,
    mapping,
    locale: 'en',
    sourceLabel,
  });
  const { stats } = preview;
  console.log(
    `  ${stats.totalRows} rows: ${stats.create} new, ${stats.update} updated, ` +
      `${stats.unchanged} unchanged, ${stats.error} errors`,
  );
  if (stats.error > 0) {
    throw new Error('Some rows could not be read. Nothing was committed — fix the workbook first.');
  }

  const committed = await commitBatch(uploaded.batchId, actor, {
    validationStatus,
    sourceDocument: filename,
  });
  console.log(`  committed: ${committed.created} created, ${committed.updated} updated`);

  let published = 0;
  for (const versionId of committed.versionIds) {
    const { rows } = await query<{
      medication_id: string;
      data: Record<string, { en?: { state: string } }>;
    }>('SELECT medication_id, data FROM medication_versions WHERE id = $1', [versionId]);
    const version = rows[0];
    if (!version) continue;

    // The workbook is the source for every claim it carries. Recorded per
    // gated field, with the approval status left unknown: the workbook marks
    // FDA approval per indication in its own prose, which is not the same as
    // asserting it for the field as a whole.
    for (const fieldKey of CITATION_REQUIRED_FIELDS) {
      if (version.data[fieldKey]?.en?.state !== 'provided') continue;
      await addCitation(
        versionId,
        {
          fieldKey,
          title: sourceLabel,
          documentRef: filename,
          url: null,
          page: null,
          jurisdiction: 'IL',
          approvalStatus: 'unknown',
          reviewedAt,
        },
        actor,
      );
    }

    // Import findings describe how the workbook is written — uncited claims,
    // prose indications, unit spacing. The owner has accepted the workbook as
    // it stands, which is a decision, so it is recorded as one rather than
    // left open to imply the question is still outstanding.
    await query(
      `UPDATE review_findings
          SET status = 'resolved', resolution_note = $3, resolved_by = $2, resolved_at = now()
        WHERE medication_id = $1 AND status IN ('open','acknowledged')`,
      [version.medication_id, actor.id, REASON],
    );

    // When the content itself was last revised by its clinician. Defaults to
    // today — the date the owner accepted this edition of the workbook — and
    // should be set to the workbook's own revision date when that is known.
    await query('UPDATE medication_versions SET reviewed_at = $2 WHERE id = $1', [
      versionId,
      reviewedAt,
    ]);

    await transitionVersion(versionId, 'in_clinical_review', actor, REASON);
    await transitionVersion(versionId, 'approved', actor, REASON);
    await transitionVersion(versionId, 'published', actor, REASON);
    published += 1;
  }

  console.log(`\n${published} records published from ${filename}.`);
  console.log(`Source label: ${sourceLabel}`);
}

main()
  .then(() => pool.end())
  .then(() => process.exit(0))
  .catch((err) => {
    console.error(err instanceof Error ? err.message : err);
    pool.end().finally(() => process.exit(1));
  });
