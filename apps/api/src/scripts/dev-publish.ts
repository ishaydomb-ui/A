/**
 * Development helper: drives a few seeded drafts through review to published
 * so the interface has visible content. Not used in production.
 */
import { pool, query } from '../db/pool.js';
import { addCitation, transitionVersion, type Actor } from '../services/catalogue.js';
import { CITATION_REQUIRED_FIELDS } from '@med/shared';

const SLUGS = process.argv.slice(2);

async function actorFor(role: string, email: string): Promise<Actor> {
  const { rows } = await query<{ id: string; email: string }>(
    `INSERT INTO users (email, email_normalized, display_name, role, status)
     VALUES ($1, $2, $3, $4::user_role, 'active')
     ON CONFLICT (email_normalized) DO UPDATE SET role = $4::user_role
     RETURNING id, email`,
    [email, email.toLowerCase(), `Dev ${role}`, role],
  );
  return { ...rows[0], role: role as never };
}

async function main(): Promise<void> {
  const editor = await actorFor('editor', 'dev-editor@localhost');
  const reviewer = await actorFor('clinical_reviewer', 'dev-reviewer@localhost');
  const admin = await actorFor('admin', 'dev-admin@localhost');

  for (const slug of SLUGS) {
    const { rows } = await query<{ id: string; data: Record<string, never> }>(
      `SELECT v.id, v.data FROM medication_versions v JOIN medications m ON m.id = v.medication_id
        WHERE m.slug = $1 AND v.state = 'draft'`,
      [slug],
    );
    if (!rows[0]) {
      console.log(`skip ${slug}: no draft`);
      continue;
    }
    const version = rows[0];

    for (const fieldKey of CITATION_REQUIRED_FIELDS) {
      const field = version.data[fieldKey] as { en?: { state: string } } | undefined;
      if (field?.en?.state !== 'provided') continue;
      await addCitation(
        version.id,
        {
          fieldKey, title: 'Development placeholder source', documentRef: null, url: null,
          page: null, jurisdiction: 'IL', approvalStatus: 'unknown', reviewedAt: '2026-01-01',
        },
        editor,
      );
    }
    await query('UPDATE medication_versions SET reviewed_at = $2 WHERE id = $1', [version.id, '2026-01-01']);
    await query(
      `UPDATE review_findings SET status = 'acknowledged' WHERE medication_id =
         (SELECT medication_id FROM medication_versions WHERE id = $1) AND severity = 'high'`,
      [version.id],
    );
    await query(
      `UPDATE review_findings SET status = 'resolved', resolution_note = 'dev seed'
        WHERE medication_id = (SELECT medication_id FROM medication_versions WHERE id = $1)
          AND severity = 'high'`,
      [version.id],
    );

    await transitionVersion(version.id, 'in_clinical_review', editor, null);
    await transitionVersion(version.id, 'approved', reviewer, null);
    await transitionVersion(version.id, 'published', admin, null);
    console.log(`published ${slug}`);
  }
}

main().then(() => pool.end()).then(() => process.exit(0)).catch((e) => { console.error(e); process.exit(1); });
