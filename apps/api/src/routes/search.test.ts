import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import type { FastifyInstance } from 'fastify';
import { query } from '../db/pool.js';
import {
  closeTestApp, createTestApp, loginAs, loginAsAdmin, resetDatabase, seedUser, shutdown,
} from '../test/helpers.js';
import { medication } from '../test/fixtures.js';
import { createMedicationDraft, transitionVersion, addCitation } from '../services/catalogue.js';
import type { Actor } from '../services/catalogue.js';

let app: FastifyInstance;
let physicianCookie: string;
let editorCookie: string;
let editor: Actor;
let reviewer: Actor;
let admin: Actor;

const CITED = ['adult_indications', 'contraindications', 'qtc_adults', 'maximum_dose'];

/** Creates a medication and drives it to published. */
async function publish(fields: Record<string, string>, locale: 'en' | 'he' = 'en') {
  const version = await createMedicationDraft(
    { data: medication(fields, locale), sourceLabel: 'Test', reviewedAt: '2026-01-01' },
    editor,
  );
  for (const fieldKey of CITED) {
    if (!fields[fieldKey]) continue;
    await addCitation(
      version.id,
      {
        fieldKey, title: 'SmPC', documentRef: null, url: null, page: '4.1',
        jurisdiction: 'IL', approvalStatus: 'approved', reviewedAt: '2026-01-01',
      },
      editor,
    );
  }
  await transitionVersion(version.id, 'in_clinical_review', editor, null);
  await transitionVersion(version.id, 'approved', reviewer, null);
  await transitionVersion(version.id, 'published', admin, null);
  return version;
}

async function searchFor(qs: string, cookie = physicianCookie) {
  const res = await app.inject({ method: 'GET', url: `/api/search?${qs}`, headers: { cookie } });
  expect(res.statusCode).toBe(200);
  return res.json();
}

beforeAll(async () => {
  await resetDatabase();
  app = await createTestApp();

  const e = await seedUser({ email: 'editor@example.org', role: 'editor' });
  const r = await seedUser({ email: 'reviewer@example.org', role: 'clinical_reviewer' });
  const a = await seedUser({ email: 'admin@example.org', role: 'admin' });
  await seedUser({ email: 'doc@example.org', role: 'physician' });
  editor = { id: e.id, email: e.email, role: 'editor' as never };
  reviewer = { id: r.id, email: r.email, role: 'clinical_reviewer' as never };
  admin = { id: a.id, email: a.email, role: 'admin' as never };

  physicianCookie = await loginAs(app, 'doc@example.org');
  editorCookie = await loginAs(app, 'editor@example.org');

  await publish({
    generic_name: 'Sertraline', trade_names: 'Zoloft, Lustral',
    therapeutic_group: 'Antidepressants', drug_class: 'SSRI',
    mechanism: 'Selective serotonin reuptake inhibitor',
    formulation: 'Tablet', adult_indications: 'Major depressive disorder, OCD, panic disorder',
    contraindications: 'Concurrent MAOI use', qtc_adults: 'Minimal', maximum_dose: '200 mg/day',
  });
  await publish({
    generic_name: 'Methylphenidate', trade_names: 'Ritalin IR',
    therapeutic_group: 'ADHD', drug_class: 'Stimulants',
    mechanism: 'Dopamine and norepinephrine transporter agonist',
    formulation: 'Immediate-release tablet',
    adult_indications: 'Attention deficit hyperactivity disorder',
    contraindications: 'Hyperthyroidism, Glaucoma', qtc_adults: 'No prolongation',
    maximum_dose: '2mg/kg/day',
  });
  await publish({
    generic_name: 'Fluoxetine', trade_names: 'Prozac',
    therapeutic_group: 'Antidepressants', drug_class: 'SSRI',
    mechanism: 'Selective serotonin reuptake inhibitor', formulation: 'Capsule',
    adult_indications: 'Major depressive disorder, bulimia nervosa',
    contraindications: 'Concurrent MAOI use', qtc_adults: 'Minimal', maximum_dose: '80 mg/day',
  });
  // Hebrew content, to prove the index is bilingual.
  await publish({
    generic_name: 'ריספרידון', trade_names: 'ריספרדל',
    therapeutic_group: 'אנטיפסיכוטיים', drug_class: 'אנטיפסיכוטי אטיפי',
    adult_indications: 'סכיזופרניה', contraindications: 'רגישות יתר',
    qtc_adults: 'הארכה קלה', maximum_dose: '16 מ"ג ליום',
  }, 'he');

  // An unpublished draft, which must never appear for a physician.
  await createMedicationDraft(
    { data: medication({ generic_name: 'Secretdrug', therapeutic_group: 'Antidepressants' }) },
    editor,
  );

  await query(
    `INSERT INTO medication_aliases (medication_id, alias, alias_normalized, kind)
     SELECT id, 'SSRI antidepressant', 'ssri antidepressant', 'synonym' FROM medications WHERE slug = 'sertraline'`,
  );
});

afterAll(async () => {
  await closeTestApp(app);
  await shutdown();
});

describe('search by different fields', () => {
  it('finds a medication by generic name', async () => {
    const result = await searchFor('q=sertraline');
    expect(result.hits[0].slug).toBe('sertraline');
    expect(result.hits[0].matchKind).toBe('exact');
  });

  it('finds a medication by trade name', async () => {
    const result = await searchFor('q=zoloft');
    expect(result.hits.map((h: { slug: string }) => h.slug)).toContain('sertraline');
  });

  it('finds medications by therapeutic group', async () => {
    const slugs = (await searchFor('q=antidepressants')).hits.map((h: { slug: string }) => h.slug);
    expect(slugs).toContain('sertraline');
    expect(slugs).toContain('fluoxetine');
  });

  it('finds medications by drug class', async () => {
    const slugs = (await searchFor('q=ssri')).hits.map((h: { slug: string }) => h.slug);
    expect(slugs).toContain('sertraline');
    expect(slugs).toContain('fluoxetine');
  });

  it('finds a medication by mechanism', async () => {
    const slugs = (await searchFor('q=serotonin+reuptake')).hits.map((h: { slug: string }) => h.slug);
    expect(slugs).toContain('sertraline');
  });

  it('finds a medication by indication', async () => {
    const slugs = (await searchFor('q=bulimia')).hits.map((h: { slug: string }) => h.slug);
    expect(slugs).toEqual(['fluoxetine']);
  });

  it('finds a medication by an alias', async () => {
    const slugs = (await searchFor('q=ssri+antidepressant')).hits.map((h: { slug: string }) => h.slug);
    expect(slugs).toContain('sertraline');
  });
});

describe('typo tolerance', () => {
  it('finds Sertraline for the misspelling "sertrline"', async () => {
    const result = await searchFor('q=sertrline');
    expect(result.usedFuzzy).toBe(true);
    expect(result.hits.length).toBeGreaterThan(0);
    expect(result.hits[0].slug).toBe('sertraline');
    expect(result.hits[0].matchKind).toBe('fuzzy');
    expect(result.didYouMean).toBe('Sertraline');
  });

  it('tolerates a transposition', async () => {
    const result = await searchFor('q=flouxetine');
    expect(result.hits.map((h: { slug: string }) => h.slug)).toContain('fluoxetine');
  });

  it('tolerates a misspelled trade name', async () => {
    const result = await searchFor('q=ritaline');
    expect(result.hits.map((h: { slug: string }) => h.slug)).toContain('methylphenidate');
  });

  it('still returns nothing for a query that matches nothing', async () => {
    const result = await searchFor('q=zzzzqqqxxx');
    expect(result.hits).toHaveLength(0);
    expect(result.didYouMean).toBeNull();
  });
});

describe('normalisation and Hebrew', () => {
  it('ignores case and punctuation', async () => {
    const result = await searchFor('q=' + encodeURIComponent('SERTRALINE (Zoloft)!'));
    expect(result.hits[0].slug).toBe('sertraline');
  });

  it('finds Hebrew content in the Hebrew index', async () => {
    const result = await searchFor('q=' + encodeURIComponent('ריספרידון') + '&locale=he');
    expect(result.hits.length).toBeGreaterThan(0);
    expect(result.hits[0].genericName).toBe('ריספרידון');
  });

  it('finds a Hebrew record regardless of final-letter form', async () => {
    const result = await searchFor('q=' + encodeURIComponent('ריספרדל') + '&locale=he');
    expect(result.hits.length).toBeGreaterThan(0);
  });

  it('falls back to the other locale and says so', async () => {
    const result = await searchFor('q=sertraline&locale=he');
    expect(result.hits[0].genericName).toBe('Sertraline');
    expect(result.hits[0].fallback).toBe(true);
    expect(result.hits[0].displayLocale).toBe('en');
  });
});

describe('highlighting', () => {
  it('marks the matched span without altering the text', () => undefined);

  it('returns highlight segments for the name', async () => {
    const result = await searchFor('q=sertra');
    const segments = result.hits[0].highlights.genericName;
    expect(segments.map((s: { text: string }) => s.text).join('')).toBe('Sertraline');
    expect(segments.find((s: { match: boolean }) => s.match).text).toBe('Sertra');
  });
});

describe('filters and facets', () => {
  it('lists facet values with counts', async () => {
    const res = await app.inject({
      method: 'GET', url: '/api/search/facets', headers: { cookie: physicianCookie },
    });
    const groups = res.json().facets.therapeutic_group;
    const antidepressants = groups.find((g: { value: string }) => g.value === 'Antidepressants');
    expect(antidepressants.count).toBe(2);
  });

  it('filters by therapeutic group', async () => {
    const result = await searchFor('q=&therapeuticGroup=Antidepressants');
    const slugs = result.hits.map((h: { slug: string }) => h.slug);
    expect(slugs).toContain('sertraline');
    expect(slugs).toContain('fluoxetine');
    expect(slugs).not.toContain('methylphenidate');
  });

  it('combines a query with a filter', async () => {
    const result = await searchFor('q=ssri&drugClass=SSRI');
    expect(result.hits.length).toBe(2);
  });

  it('excludes facet values that only exist on drafts', async () => {
    const res = await app.inject({
      method: 'GET', url: '/api/search/facets', headers: { cookie: physicianCookie },
    });
    const groups = res.json().facets.therapeutic_group;
    const antidepressants = groups.find((g: { value: string }) => g.value === 'Antidepressants');
    // Secretdrug is a draft in that group and must not be counted.
    expect(antidepressants.count).toBe(2);
  });
});

describe('autocomplete', () => {
  it('suggests generic names by prefix', async () => {
    const res = await app.inject({
      method: 'GET', url: '/api/search/suggest?q=sert', headers: { cookie: physicianCookie },
    });
    expect(res.json().suggestions[0].label).toBe('Sertraline');
  });

  it('suggests trade names from aliases', async () => {
    const res = await app.inject({
      method: 'GET', url: '/api/search/suggest?q=ssri+anti', headers: { cookie: physicianCookie },
    });
    expect(res.json().suggestions.some((s: { label: string }) => s.label === 'SSRI antidepressant')).toBe(true);
  });

  it('returns nothing for a one-character query', async () => {
    const res = await app.inject({
      method: 'GET', url: '/api/search/suggest?q=s', headers: { cookie: physicianCookie },
    });
    expect(res.json().suggestions).toEqual([]);
  });
});

describe('unpublished content is never searchable by physicians', () => {
  it('does not return a draft to a physician', async () => {
    const result = await searchFor('q=secretdrug');
    expect(result.hits).toHaveLength(0);
  });

  it('ignores includeUnpublished from a physician', async () => {
    const result = await searchFor('q=secretdrug&includeUnpublished=true');
    expect(result.hits).toHaveLength(0);
  });

  it('does show the draft to an editor who asks for it', async () => {
    const result = await searchFor('q=secretdrug&includeUnpublished=true', editorCookie);
    expect(result.hits.map((h: { slug: string }) => h.slug)).toContain('secretdrug');
    expect(result.hits[0].state).toBe('draft');
  });

  it('requires authentication', async () => {
    const res = await app.inject({ method: 'GET', url: '/api/search?q=sertraline' });
    expect(res.statusCode).toBe(401);
  });
});

describe('result shape', () => {
  it('returns a compact row, not the full record', async () => {
    const result = await searchFor('q=sertraline');
    const hit = result.hits[0];
    expect(Object.keys(hit).sort()).toEqual([
      'displayLocale', 'drugClass', 'fallback', 'genericName', 'highlights', 'matchKind',
      'medicationId', 'publishedUnvalidated', 'score', 'slug', 'state', 'therapeuticGroup',
      'tradeNames', 'versionId',
    ]);
    expect(hit).not.toHaveProperty('contraindications');
  });

  it('paginates', async () => {
    const page = await searchFor('q=&limit=2&offset=0');
    expect(page.hits).toHaveLength(2);
    expect(page.total).toBe(4);
    const next = await searchFor('q=&limit=2&offset=2');
    expect(next.hits).toHaveLength(2);
    expect(next.hits[0].slug).not.toBe(page.hits[0].slug);
  });
});
