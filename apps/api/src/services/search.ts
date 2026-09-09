import { MAX_COMPARE, highlight, normalizeText, tokenize, type Locale } from '@med/shared';
import { query, withTransaction } from '../db/pool.js';

export interface SearchFilters {
  therapeuticGroup?: string[];
  drugClass?: string[];
  drugFamily?: string[];
  formulation?: string[];
}

export interface SearchOptions {
  q: string;
  locale: Locale;
  filters: SearchFilters;
  limit: number;
  offset: number;
  /** Only ever true for a caller holding catalogue:read_unpublished. */
  includeUnpublished: boolean;
}

export interface SearchHit {
  slug: string;
  medicationId: string;
  versionId: string;
  state: string;
  score: number;
  /** How the row was found, so the UI can explain a fuzzy match. */
  matchKind: 'exact' | 'prefix' | 'text' | 'fuzzy';
  genericName: string | null;
  tradeNames: string | null;
  therapeuticGroup: string | null;
  drugClass: string | null;
  /** Locale the displayed text came from, when it differs from the request. */
  displayLocale: Locale;
  fallback: boolean;
  highlights: {
    genericName: ReturnType<typeof highlight>;
    tradeNames: ReturnType<typeof highlight>;
  };
}

export interface SearchResult {
  hits: SearchHit[];
  total: number;
  /** Set when the query only matched approximately. */
  didYouMean: string | null;
  usedFuzzy: boolean;
}

interface DisplayField {
  value: { state: string; text: string | null };
  locale: Locale;
  fallback: boolean;
}

interface RawHit {
  slug: string;
  medication_id: string;
  version_id: string;
  state: string;
  locale: Locale;
  display: {
    slug: string;
    genericName: DisplayField;
    tradeNames: DisplayField;
    therapeuticGroup: DisplayField;
    drugClass: DisplayField;
  };
  rank: number;
  similarity: number;
  name_text: string;
  total: number;
}

const textOf = (field: DisplayField | undefined): string | null =>
  field && field.value.state === 'provided' ? field.value.text : null;

/**
 * Builds a websearch-style tsquery with a prefix match on the final term, so
 * results narrow as the user types.
 */
function toTsQuery(terms: string[]): string {
  if (terms.length === 0) return '';
  return terms
    .map((term, i) => (i === terms.length - 1 ? `${term}:*` : term))
    .join(' & ');
}

/**
 * Word-similarity floor for the fuzzy pass. Chosen to admit a one-character
 * slip or transposition in a drug name while still rejecting unrelated input.
 */
const FUZZY_THRESHOLD = 0.4;

const STATE_FILTER = {
  published: `state = 'published'`,
  all: `state IN ('published','draft','in_clinical_review','changes_requested','approved')`,
};

/**
 * Two-pass search.
 *
 * The first pass is a full-text match with prefix completion, which handles
 * generic names, trade names, classes, mechanisms, indications and keywords.
 * If that returns nothing, a second pass uses trigram similarity so that a
 * misspelling such as "sertrline" still finds Sertraline — the source site's
 * search returned nothing for exactly that query.
 */
export async function search(options: SearchOptions): Promise<SearchResult> {
  const terms = tokenize(options.q);
  const normalizedQuery = normalizeText(options.q);
  const stateSql = options.includeUnpublished ? STATE_FILTER.all : STATE_FILTER.published;

  const facetJoins: string[] = [];
  const params: unknown[] = [options.locale];
  const bind = (value: unknown): string => {
    params.push(value);
    return `$${params.length}`;
  };

  for (const [facet, values] of Object.entries(options.filters)) {
    if (!values?.length) continue;
    const facetName = facet.replace(/[A-Z]/g, (c) => `_${c.toLowerCase()}`);
    const normalizedValues = (values as string[]).map((v) => normalizeText(v));
    facetJoins.push(`
      AND EXISTS (
        SELECT 1 FROM search_facets f
         WHERE f.version_id = d.version_id
           AND f.facet = ${bind(facetName)}
           AND f.value_normalized = ANY(${bind(normalizedValues)}::text[])
      )`);
  }

  const limitParam = bind(options.limit);
  const offsetParam = bind(options.offset);

  // --- Pass 1: full text -----------------------------------------------------
  let rows: RawHit[] = [];
  let usedFuzzy = false;

  if (terms.length > 0) {
    const tsQuery = toTsQuery(terms);
    const ftsParams = [...params, tsQuery];
    const tsParam = `$${ftsParams.length}`;
    const { rows: ftsRows } = await query<RawHit>(
      `SELECT d.version_id, d.medication_id, d.state::text AS state, d.locale, d.display,
              m.slug, d.name_text,
              ts_rank_cd(d.tsv, to_tsquery('simple', ${tsParam})) AS rank,
              0::float AS similarity,
              count(*) OVER () AS total
         FROM search_documents d
         JOIN medications m ON m.id = d.medication_id
        WHERE d.locale = $1
          AND d.${stateSql}
          AND d.tsv @@ to_tsquery('simple', ${tsParam})
          ${facetJoins.join('\n')}
        ORDER BY rank DESC, d.name_text
        LIMIT ${limitParam} OFFSET ${offsetParam}`,
      ftsParams,
    );
    rows = ftsRows;
  } else {
    // Empty query: browse everything, filtered.
    const { rows: allRows } = await query<RawHit>(
      `SELECT d.version_id, d.medication_id, d.state::text AS state, d.locale, d.display,
              m.slug, d.name_text, 0::float AS rank, 0::float AS similarity,
              count(*) OVER () AS total
         FROM search_documents d
         JOIN medications m ON m.id = d.medication_id
        WHERE d.locale = $1
          AND d.${stateSql}
          ${facetJoins.join('\n')}
        ORDER BY d.name_text
        LIMIT ${limitParam} OFFSET ${offsetParam}`,
      params,
    );
    rows = allRows;
  }

  // --- Pass 2: trigram fallback ---------------------------------------------
  if (rows.length === 0 && normalizedQuery.length >= 3) {
    usedFuzzy = true;
    const fuzzyParams = [...params, normalizedQuery];
    const qParam = `$${fuzzyParams.length}`;
    const fuzzyRows = await withTransaction(async (client) => {
      // Postgres defaults to 0.6, which rejects a single transposition such as
      // "flouxetine" for "fluoxetine" (0.47) while nonsense scores around 0.1.
      // SET LOCAL keeps the change to this transaction, so a pooled connection
      // never carries it into another query.
      await client.query(`SET LOCAL pg_trgm.word_similarity_threshold = ${FUZZY_THRESHOLD}`);
      const { rows: found } = await client.query<RawHit>(
      `SELECT d.version_id, d.medication_id, d.state::text AS state, d.locale, d.display,
              m.slug, d.name_text, 0::float AS rank,
              GREATEST(
                similarity(d.name_text, ${qParam}),
                COALESCE((SELECT max(similarity(a.alias_normalized, ${qParam}))
                            FROM medication_aliases a
                           WHERE a.medication_id = d.medication_id), 0)
              ) AS similarity,
              count(*) OVER () AS total
         FROM search_documents d
         JOIN medications m ON m.id = d.medication_id
        WHERE d.locale = $1
          AND d.${stateSql}
          AND (
            d.name_text %> ${qParam}
            OR EXISTS (SELECT 1 FROM medication_aliases a
                        WHERE a.medication_id = d.medication_id
                          AND a.alias_normalized %> ${qParam})
          )
          ${facetJoins.join('\n')}
        ORDER BY similarity DESC, d.name_text
        LIMIT ${limitParam} OFFSET ${offsetParam}`,
        fuzzyParams as never[],
      );
      return found;
    });
    rows = fuzzyRows;
  }

  const hits: SearchHit[] = rows.map((row) => {
    const genericName = textOf(row.display?.genericName);
    const tradeNames = textOf(row.display?.tradeNames);
    const normalizedName = normalizeText(genericName ?? '');

    let matchKind: SearchHit['matchKind'] = 'text';
    if (usedFuzzy) matchKind = 'fuzzy';
    else if (normalizedName === normalizedQuery) matchKind = 'exact';
    else if (normalizedQuery && normalizedName.startsWith(normalizedQuery)) matchKind = 'prefix';

    return {
      slug: row.slug,
      medicationId: row.medication_id,
      versionId: row.version_id,
      state: row.state,
      score: usedFuzzy ? Number(row.similarity) : Number(row.rank),
      matchKind,
      genericName,
      tradeNames,
      therapeuticGroup: textOf(row.display?.therapeuticGroup),
      drugClass: textOf(row.display?.drugClass),
      displayLocale: row.display?.genericName?.locale ?? options.locale,
      fallback: row.display?.genericName?.fallback ?? false,
      highlights: {
        genericName: highlight(genericName ?? '', terms),
        tradeNames: highlight(tradeNames ?? '', terms),
      },
    };
  });

  // Exact and prefix matches outrank body-text matches regardless of tsrank.
  const order = { exact: 0, prefix: 1, text: 2, fuzzy: 3 };
  hits.sort((a, b) => order[a.matchKind] - order[b.matchKind] || b.score - a.score);

  return {
    hits,
    total: rows[0] ? Number(rows[0].total) : 0,
    didYouMean: usedFuzzy && hits[0]?.genericName ? hits[0].genericName : null,
    usedFuzzy,
  };
}

/** Type-ahead over names and aliases only, so it stays fast and predictable. */
export async function suggest(
  q: string,
  locale: Locale,
  includeUnpublished: boolean,
  limit = 8,
): Promise<Array<{ slug: string; label: string; kind: string }>> {
  const normalized = normalizeText(q);
  if (normalized.length < 2) return [];
  const stateSql = includeUnpublished ? STATE_FILTER.all : STATE_FILTER.published;

  const { rows } = await query<{ slug: string; label: string; kind: string; score: number }>(
    `WITH names AS (
       SELECT m.slug,
              d.display #>> '{genericName,value,text}' AS label,
              'generic' AS kind,
              CASE WHEN d.name_text LIKE $2 || '%' THEN 1.0
                   ELSE similarity(d.name_text, $2) END AS score
         FROM search_documents d
         JOIN medications m ON m.id = d.medication_id
        WHERE d.locale = $1 AND d.${stateSql}
          AND (d.name_text LIKE $2 || '%' OR d.name_text %> $2)
     ),
     aliases AS (
       SELECT m.slug, a.alias AS label, a.kind::text AS kind,
              CASE WHEN a.alias_normalized LIKE $2 || '%' THEN 0.95
                   ELSE similarity(a.alias_normalized, $2) END AS score
         FROM medication_aliases a
         JOIN medications m ON m.id = a.medication_id
         JOIN search_documents d ON d.medication_id = m.id AND d.locale = $1
        WHERE d.${stateSql}
          AND (a.alias_normalized LIKE $2 || '%' OR a.alias_normalized %> $2)
     )
     SELECT DISTINCT ON (slug, label) slug, label, kind, score
       FROM (SELECT * FROM names UNION ALL SELECT * FROM aliases) s
      WHERE label IS NOT NULL
      ORDER BY slug, label, score DESC`,
    [locale, normalized],
  );

  return rows
    .sort((a, b) => b.score - a.score)
    .slice(0, limit)
    .map(({ slug, label, kind }) => ({ slug, label, kind }));
}

/** Distinct filter values, with the count of records behind each. */
export async function facets(
  includeUnpublished: boolean,
): Promise<Record<string, Array<{ value: string; count: number }>>> {
  const stateSql = includeUnpublished ? STATE_FILTER.all : STATE_FILTER.published;
  const { rows } = await query<{ facet: string; value: string; count: number }>(
    `SELECT facet, value, count(DISTINCT medication_id)::int AS count
       FROM search_facets
      WHERE ${stateSql}
      GROUP BY facet, value
      ORDER BY facet, count DESC, value`,
  );
  const out: Record<string, Array<{ value: string; count: number }>> = {};
  for (const row of rows) {
    (out[row.facet] ??= []).push({ value: row.value, count: row.count });
  }
  return out;
}

export { MAX_COMPARE };
