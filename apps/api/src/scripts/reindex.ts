/**
 * Rebuilds the search index from the catalogue as it already stands.
 *
 *   node dist/scripts/reindex.js
 *
 * The index is derived data, so rebuilding it changes nothing a clinician
 * reads — but which fields feed it does change from time to time, and until
 * this existed the only way to pick such a change up was to re-run the
 * formulary load, which creates a new version of every record. Bumping the
 * clinical history of 44 medications to fix a search bug is the wrong trade.
 */
import { pool, query } from '../db/pool.js';
import { indexVersion } from '../services/indexer.js';
import type { MedicationData } from '@med/shared';

async function main(): Promise<void> {
  const { rows } = await query<{
    id: string;
    medication_id: string;
    slug: string;
    state: string;
    data: MedicationData;
    published_unvalidated: boolean;
  }>(
    `SELECT v.id, v.medication_id, m.slug, v.state, v.data, v.published_unvalidated
       FROM medication_versions v
       JOIN medications m ON m.id = v.medication_id
      WHERE v.state <> 'archived'
      ORDER BY m.slug`,
  );

  for (const version of rows) {
    const { rows: aliases } = await query<{ alias: string }>(
      'SELECT alias FROM medication_aliases WHERE medication_id = $1',
      [version.medication_id],
    );
    await indexVersion(
      version.id,
      version.medication_id,
      version.slug,
      version.state,
      version.data,
      aliases.map((a) => a.alias),
      undefined,
      version.published_unvalidated,
    );
  }

  console.log(`Reindexed ${rows.length} versions.`);
}

main()
  .then(() => pool.end())
  .then(() => process.exit(0))
  .catch((err) => {
    console.error(err instanceof Error ? err.message : err);
    pool.end().finally(() => process.exit(1));
  });
