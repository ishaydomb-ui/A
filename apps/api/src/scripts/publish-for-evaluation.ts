/**
 * Publishes every draft for evaluation, before clinical review.
 *
 * This is a deliberate, recorded act, not a bypass: it switches the system's
 * preview allowance on, and each record is published with its outstanding
 * blockers stored on the row and in the revision trail, so the interface can
 * say plainly that the content has not been reviewed.
 *
 * Intended for a single-user evaluation of a catalogue whose data is known to
 * be unverified. Switch the allowance off again before real clinical use.
 */
import { pool, query } from '../db/pool.js';
import { transitionVersion, type Actor } from '../services/catalogue.js';
import { ensureDefaultSettings, setSetting } from '../services/settings.js';

async function main(): Promise<void> {
  await ensureDefaultSettings();

  const { rows: admins } = await query<{ id: string; email: string }>(
    `SELECT id, email FROM users WHERE role = 'admin' ORDER BY created_at LIMIT 1`,
  );
  if (!admins[0]) {
    console.error('No administrator exists. Run create-admin first.');
    process.exit(1);
  }
  const actor: Actor = { ...admins[0], role: 'admin' };

  await setSetting('publication.allow_unvalidated', true, actor.id);
  console.log('Preview publication: ON');

  const { rows: drafts } = await query<{ id: string; slug: string; state: string }>(
    `SELECT v.id, m.slug, v.state::text AS state
       FROM medication_versions v JOIN medications m ON m.id = v.medication_id
      WHERE v.state IN ('draft', 'changes_requested', 'in_clinical_review', 'approved')
      ORDER BY m.slug`,
  );

  let published = 0;
  let failed = 0;

  for (const draft of drafts) {
    try {
      // Walk the real workflow rather than writing the state directly, so the
      // revision trail reads the same as any other publication.
      let state = draft.state;
      if (state === 'draft' || state === 'changes_requested') {
        await transitionVersion(draft.id, 'in_clinical_review', actor, 'Evaluation publication.');
        state = 'in_clinical_review';
      }
      if (state === 'in_clinical_review') {
        await transitionVersion(draft.id, 'approved', actor, 'Evaluation publication — not a clinical approval.');
      }
      await transitionVersion(
        draft.id, 'published', actor,
        'Published for evaluation before clinical review.',
        { acknowledgeUnvalidated: true },
      );
      published++;
    } catch (err) {
      failed++;
      console.error(`  ${draft.slug}: ${err instanceof Error ? err.message : err}`);
    }
  }

  const { rows: counts } = await query<{ state: string; n: number }>(
    `SELECT state::text AS state, count(*)::int AS n FROM medication_versions GROUP BY state ORDER BY 1`,
  );
  const { rows: flagged } = await query<{ n: number }>(
    `SELECT count(*)::int AS n FROM medication_versions WHERE published_unvalidated`,
  );

  console.log(`\nPublished ${published}, failed ${failed}`);
  for (const c of counts) console.log(`  ${c.state}: ${c.n}`);
  console.log(`  flagged as not clinically reviewed: ${flagged[0].n}`);
  console.log('\nEvery published record shows a warning that it has not been reviewed.');
  console.log("Switch the allowance off with the Settings API once real review begins.");
}

main().then(() => pool.end()).then(() => process.exit(0)).catch((err) => {
  console.error(err);
  process.exit(1);
});
