import { LOCALES, normalizeText, resolveField, type Locale, type MedicationData } from '@med/shared';
import { query, type Queryable } from '../db/pool.js';

/** Fields that make up each weight class of the search index. */
const NAME_FIELDS = ['generic_name', 'trade_names'];
const CLASS_FIELDS = ['therapeutic_group', 'drug_class', 'drug_family', 'mechanism'];
const BODY_FIELDS = [
  'adult_indications', 'pediatric_indications', 'side_effects',
  'contraindications', 'monitoring_tests', 'clinical_notes', 'formulation',
];

/** Facets offered as filters in the UI. */
const FACET_FIELDS = ['therapeutic_group', 'drug_class', 'drug_family', 'formulation'] as const;

function textOf(data: MedicationData, key: string, locale: Locale): string {
  const value = data[key]?.[locale];
  return value?.state === 'provided' && value.text ? value.text : '';
}

/**
 * Search text for a locale, falling back to the other locale when this one
 * has no content.
 *
 * Without this a clinician searching in the Hebrew interface could not find a
 * record whose content is only in English — which is most of them, since drug
 * names are routinely written in Latin script. This mirrors how resolveField
 * already picks the text to display.
 */
function searchTextOf(data: MedicationData, key: string, locale: Locale): string {
  const own = textOf(data, key, locale);
  if (own) return own;
  return textOf(data, key, locale === 'en' ? 'he' : 'en');
}

function joinNormalized(data: MedicationData, keys: string[], locale: Locale): string {
  return normalizeText(keys.map((k) => searchTextOf(data, k, locale)).filter(Boolean).join(' \n '));
}

/**
 * Rebuilds the derived search rows for one version.
 *
 * Everything here is derived, never authoritative: the index is normalised
 * for matching while `display` keeps the original text so results are shown
 * exactly as stored.
 */
export async function indexVersion(
  versionId: string,
  medicationId: string,
  slug: string,
  state: string,
  data: MedicationData,
  aliases: string[],
  client?: Queryable,
  publishedUnvalidated = false,
): Promise<void> {
  await query('DELETE FROM search_documents WHERE version_id = $1', [versionId], client);
  await query('DELETE FROM search_facets WHERE version_id = $1', [versionId], client);

  const aliasText = normalizeText(aliases.join(' \n '));

  for (const locale of LOCALES) {
    const nameText = [joinNormalized(data, NAME_FIELDS, locale), aliasText]
      .filter(Boolean)
      .join(' ');
    const classText = joinNormalized(data, CLASS_FIELDS, locale);
    const bodyText = joinNormalized(data, BODY_FIELDS, locale);

    // The compact result row is rendered straight from this, so it holds the
    // original text with a note when it came from the other locale.
    const display = {
      slug,
      publishedUnvalidated,
      genericName: resolveField(data, 'generic_name', locale),
      tradeNames: resolveField(data, 'trade_names', locale),
      therapeuticGroup: resolveField(data, 'therapeutic_group', locale),
      drugClass: resolveField(data, 'drug_class', locale),
    };

    await query(
      `INSERT INTO search_documents
         (version_id, medication_id, locale, state, name_text, class_text, body_text, display)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8)`,
      [versionId, medicationId, locale, state, nameText, classText, bodyText, JSON.stringify(display)],
      client,
    );
  }

  // Facets are language-neutral: a value is offered as a filter exactly as it
  // is stored, in whichever locale supplied it.
  const seen = new Set<string>();
  for (const facet of FACET_FIELDS) {
    for (const locale of LOCALES) {
      const value = textOf(data, facet, locale).trim();
      if (!value) continue;
      const valueNormalized = normalizeText(value);
      if (!valueNormalized) continue;
      const dedupeKey = `${facet}|${valueNormalized}`;
      if (seen.has(dedupeKey)) continue;
      seen.add(dedupeKey);
      await query(
        `INSERT INTO search_facets (version_id, medication_id, state, facet, value, value_normalized)
         VALUES ($1, $2, $3, $4, $5, $6)
         ON CONFLICT (version_id, facet, value_normalized) DO NOTHING`,
        [versionId, medicationId, state, facet, value, valueNormalized],
        client,
      );
    }
  }
}

/** Keeps the index's state column in step after a workflow transition. */
export async function reindexState(
  versionId: string,
  state: string,
  client?: Queryable,
  publishedUnvalidated?: boolean,
): Promise<void> {
  await query('UPDATE search_documents SET state = $2 WHERE version_id = $1', [versionId, state], client);
  await query('UPDATE search_facets   SET state = $2 WHERE version_id = $1', [versionId, state], client);
  if (publishedUnvalidated !== undefined) {
    await query(
      `UPDATE search_documents
          SET display = jsonb_set(display, '{publishedUnvalidated}', to_jsonb($2::boolean))
        WHERE version_id = $1`,
      [versionId, publishedUnvalidated],
      client,
    );
  }
}

export async function removeFromIndex(versionId: string, client?: Queryable): Promise<void> {
  await query('DELETE FROM search_documents WHERE version_id = $1', [versionId], client);
  await query('DELETE FROM search_facets WHERE version_id = $1', [versionId], client);
}
