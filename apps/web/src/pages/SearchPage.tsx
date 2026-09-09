import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useI18n } from '../i18n.ts';
import { useAuth } from '../lib/auth.tsx';
import { ApiError, api, qs } from '../lib/api.ts';
import type { Facets, SearchResponse } from '../lib/types.ts';
import { SearchBar } from '../components/SearchBar.tsx';
import { FilterPanel, EMPTY_FILTERS, type FilterKey, type FilterState } from '../components/FilterPanel.tsx';
import { ResultList } from '../components/ResultList.tsx';
import { CompareTray } from '../components/CompareTray.tsx';
import { Notice } from '../components/Notice.tsx';
import { Spinner } from '../components/Spinner.tsx';

const PAGE_SIZE = 25;
const FILTER_KEYS: FilterKey[] = ['therapeuticGroup', 'drugClass', 'drugFamily', 'formulation'];

export function SearchPage() {
  const { t, locale } = useI18n();
  const { can } = useAuth();
  // The query lives in the URL so a search can be bookmarked, shared with a
  // colleague, and survives the back button.
  const [params, setParams] = useSearchParams();

  const query = params.get('q') ?? '';
  const [draft, setDraft] = useState(query);
  const filters = useMemo<FilterState>(() => {
    const next = { ...EMPTY_FILTERS };
    for (const key of FILTER_KEYS) {
      const raw = params.get(key);
      next[key] = raw ? raw.split(',').filter(Boolean) : [];
    }
    return next;
  }, [params]);

  const [results, setResults] = useState<SearchResponse | null>(null);
  const [facets, setFacets] = useState<Facets>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [limit, setLimit] = useState(PAGE_SIZE);
  const [selected, setSelected] = useState<string[]>([]);

  const canSeeDrafts = can('catalogue:read_unpublished');
  const includeUnpublished = canSeeDrafts && params.get('drafts') === '1';

  useEffect(() => setDraft(query), [query]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);

    api
      .get<SearchResponse>(
        `/api/search${qs({
          q: query,
          locale,
          limit,
          includeUnpublished,
          therapeuticGroup: filters.therapeuticGroup,
          drugClass: filters.drugClass,
          drugFamily: filters.drugFamily,
          formulation: filters.formulation,
        })}`,
        controller.signal,
      )
      .then(setResults)
      .catch((err: unknown) => {
        if ((err as Error).name === 'AbortError') return;
        setError(err instanceof ApiError ? err.message : t.errorGeneric);
      })
      .finally(() => setLoading(false));

    return () => controller.abort();
  }, [query, locale, limit, includeUnpublished, filters, t]);

  useEffect(() => {
    api
      .get<{ facets: Facets }>(`/api/search/facets${qs({ includeUnpublished })}`)
      .then((res) => setFacets(res.facets))
      .catch(() => undefined);
  }, [includeUnpublished]);

  const runSearch = useCallback(
    (next: string) => {
      const params2 = new URLSearchParams(params);
      if (next) params2.set('q', next);
      else params2.delete('q');
      setLimit(PAGE_SIZE);
      setParams(params2, { replace: false });
    },
    [params, setParams],
  );

  const applyFilters = useCallback(
    (next: FilterState) => {
      const params2 = new URLSearchParams(params);
      for (const key of FILTER_KEYS) {
        if (next[key].length > 0) params2.set(key, next[key].join(','));
        else params2.delete(key);
      }
      setLimit(PAGE_SIZE);
      setParams(params2);
    },
    [params, setParams],
  );

  const toggleCompare = useCallback((slug: string) => {
    setSelected((current) =>
      current.includes(slug)
        ? current.filter((s) => s !== slug)
        : current.length >= (results?.maxCompare ?? 3)
          ? current
          : [...current, slug],
    );
  }, [results?.maxCompare]);

  const hits = results?.hits ?? [];
  const maxCompare = results?.maxCompare ?? 3;

  return (
    <>
      <h1>{t.navSearch}</h1>

      <SearchBar
        value={draft}
        onChange={setDraft}
        onSubmit={runSearch}
        includeUnpublished={includeUnpublished}
      />

      {canSeeDrafts && (
        <label className="checkbox-row" style={{ maxWidth: 420 }}>
          <input
            type="checkbox"
            checked={includeUnpublished}
            onChange={(event) => {
              const params2 = new URLSearchParams(params);
              if (event.target.checked) params2.set('drafts', '1');
              else params2.delete('drafts');
              setParams(params2);
            }}
          />
          <span>Include unpublished drafts</span>
        </label>
      )}

      <div className="search-layout">
        <FilterPanel facets={facets} filters={filters} onChange={applyFilters} />

        <div>
          {error && <Notice tone="error">{error}</Notice>}

          {results?.usedFuzzy && results.didYouMean && (
            <Notice tone="info">
              {t.approximateMatch} “{query}”. — <strong>{results.didYouMean}</strong>
            </Notice>
          )}

          {/* Announced politely so a screen-reader user hears the new count
              without the focus being yanked away from the input. */}
          <div className="results-meta" role="status" aria-live="polite">
            {loading ? <Spinner /> : <span>{t.resultsCount(results?.total ?? 0)}</span>}
          </div>

          <section aria-label={t.resultsRegion}>
            {!loading && hits.length === 0 && (
              <div className="empty-state">
                <p>
                  <strong>{t.noResults}</strong>
                </p>
                <p className="small">{t.noResultsHint}</p>
              </div>
            )}

            {hits.length > 0 && (
              <ResultList
                hits={hits}
                selected={selected}
                onToggleCompare={toggleCompare}
                maxCompare={maxCompare}
              />
            )}
          </section>

          {results && results.total > hits.length && (
            <p className="center" style={{ marginBlockStart: 16 }}>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => setLimit((n) => n + PAGE_SIZE)}
              >
                {t.loadMore}
              </button>
            </p>
          )}
        </div>
      </div>

      <CompareTray
        selected={selected}
        onRemove={(slug) => setSelected((s) => s.filter((x) => x !== slug))}
        onClear={() => setSelected([])}
        maxCompare={maxCompare}
      />
    </>
  );
}
