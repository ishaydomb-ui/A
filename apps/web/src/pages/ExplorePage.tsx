import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useI18n } from '../i18n.ts';
import { api } from '../lib/api.ts';
import { readRecentlyViewed, type RecentlyViewedEntry } from '../lib/recentlyViewed.ts';
import type { Facets } from '../lib/types.ts';
import { SearchBar } from '../components/SearchBar.tsx';
import { Spinner } from '../components/Spinner.tsx';

const TILE_SURFACES = ['surface-lilac', 'surface-pink', 'surface-blue'] as const;
const AREA_TILE_COUNT = 4;
const SUGGESTION_COUNT = 4;

/**
 * The home screen: a search-and-discovery front door rather than an
 * administrative list. The full alphabetical catalogue is one tap away
 * (browseAll), but it is not what greets anyone who signs in.
 */
export function ExplorePage() {
  const { t } = useI18n();
  const navigate = useNavigate();

  const [query, setQuery] = useState('');
  const [facets, setFacets] = useState<Facets | null>(null);
  const [recent, setRecent] = useState<RecentlyViewedEntry[]>([]);

  useEffect(() => {
    setRecent(readRecentlyViewed());
  }, []);

  useEffect(() => {
    api
      .get<{ facets: Facets }>('/api/search/facets')
      .then((res) => setFacets(res.facets))
      .catch(() => setFacets({}));
  }, []);

  function runSearch(next: string) {
    const params = new URLSearchParams();
    if (next) params.set('q', next);
    navigate(`/catalogue${params.size ? `?${params}` : ''}`);
  }

  function exploreArea(value: string) {
    navigate(`/catalogue?therapeuticGroup=${encodeURIComponent(value)}`);
  }

  const areas = (facets?.['therapeutic_group'] ?? []).slice(0, AREA_TILE_COUNT);
  const suggestions = (facets?.['therapeutic_group'] ?? []).slice(0, SUGGESTION_COUNT);

  return (
    <div className="explore-page">
      <p className="explore-eyebrow">{t.exploreEyebrow}</p>
      <h1 className="explore-question">{t.exploreQuestion}</h1>

      <SearchBar value={query} onChange={setQuery} onSubmit={runSearch} includeUnpublished={false} />

      {suggestions.length > 0 && (
        <div className="explore-suggestions">
          <span className="small muted">{t.suggestedSearches}</span>
          {suggestions.map((facet) => (
            <button
              key={facet.value}
              type="button"
              className="chip-button"
              onClick={() => exploreArea(facet.value)}
            >
              {facet.value}
            </button>
          ))}
        </div>
      )}

      <section aria-labelledby="explore-areas-heading" style={{ marginBlockStart: 32 }}>
        <div className="row" style={{ marginBlockEnd: 12 }}>
          <h2 id="explore-areas-heading" style={{ margin: 0 }}>
            {t.exploreByArea}
          </h2>
          <span className="spacer" />
          <Link to="/catalogue">{t.viewAll}</Link>
        </div>

        {facets === null ? (
          <Spinner />
        ) : areas.length === 0 ? null : (
          <div className="area-tile-grid">
            {areas.map((facet, i) => (
              <button
                key={facet.value}
                type="button"
                className={`area-tile ${TILE_SURFACES[i % TILE_SURFACES.length]}`}
                onClick={() => exploreArea(facet.value)}
              >
                <span className="area-tile-name">{facet.value}</span>
                <span className="area-tile-count">{t.resultsCount(facet.count)}</span>
              </button>
            ))}
          </div>
        )}
      </section>

      {recent.length > 0 && (
        <section aria-labelledby="explore-recent-heading" style={{ marginBlockStart: 32 }}>
          <h2 id="explore-recent-heading">{t.recentlyViewed}</h2>
          <ul className="recent-list">
            {recent.map((entry) => (
              <li key={entry.slug}>
                <Link to={`/medications/${encodeURIComponent(entry.slug)}`} className="recent-row">
                  <span className="recent-avatar" aria-hidden="true">
                    {entry.genericName.charAt(0).toUpperCase()}
                  </span>
                  <span className="recent-text">
                    <span className="recent-name">{entry.genericName}</span>
                    {entry.tradeNames && <span className="recent-trade">{entry.tradeNames}</span>}
                  </span>
                  <span className="recent-chevron" aria-hidden="true">
                    ›
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      <p style={{ marginBlockStart: 32 }}>
        <Link to="/catalogue" className="btn btn-secondary">
          {t.browseAll}
        </Link>
      </p>
    </div>
  );
}
