import { Link } from 'react-router-dom';
import { useI18n } from '../i18n.ts';
import type { SearchHit } from '../lib/types.ts';
import { Highlight } from './Highlight.tsx';

/**
 * Placeholder rows shown only on the very first load of a query, before any
 * hit has ever arrived — a later refetch keeps showing the previous results
 * until the new ones are ready, so this never appears mid-search.
 */
export function ResultListSkeleton({ count = 6 }: { count?: number }) {
  return (
    <ul className="result-list" aria-hidden="true">
      {Array.from({ length: count }, (_, i) => (
        // Deliberately NOT .result-row: that class means "an actual result"
        // to the rest of the app (and to tests locating real rows), and a
        // placeholder must never be mistaken for one mid-swap.
        <li key={i} className="skeleton-row">
          <span className="skeleton-row-main">
            <span className="skeleton-block" style={{ inlineSize: '38%', blockSize: '0.95em' }} />
            <span className="skeleton-block" style={{ inlineSize: '22%', blockSize: '0.7em', marginBlockStart: 6 }} />
          </span>
          <span className="skeleton-block" style={{ inlineSize: 44, blockSize: 44, borderRadius: 'var(--radius)' }} />
        </li>
      ))}
    </ul>
  );
}

/**
 * The compact result list.
 *
 * One scannable row per medication rather than a large card, so a phone shows
 * many results at once — the source site rendered 44 tall cards on a single
 * page, which the audit flagged as unusable.
 */
export function ResultList({
  hits,
  selected,
  onToggleCompare,
  maxCompare,
}: {
  hits: SearchHit[];
  selected: string[];
  onToggleCompare(slug: string): void;
  maxCompare: number;
}) {
  const { t } = useI18n();

  return (
    <ul className="result-list">
      {hits.map((hit) => {
        const isSelected = selected.includes(hit.slug);
        const atLimit = !isSelected && selected.length >= maxCompare;
        const name = hit.genericName ?? hit.slug;
        // A class equal to its own group name (e.g. "Antipsychotics —
        // Antipsychotics") says nothing a single tag doesn't already say.
        const sameGroupAndClass =
          !!hit.therapeuticGroup &&
          !!hit.drugClass &&
          hit.therapeuticGroup.trim().toLowerCase() === hit.drugClass.trim().toLowerCase();

        return (
          <li key={hit.slug} className="result-row">
            <Link className="result-main" to={`/medications/${encodeURIComponent(hit.slug)}`}>
              <span className="result-name">
                <Highlight segments={hit.highlights.genericName} fallback={name} />
                {hit.tradeNames && (
                  <span className="result-trade">
                    <Highlight segments={hit.highlights.tradeNames} fallback={hit.tradeNames} />
                  </span>
                )}
              </span>
              <span className="result-meta">
                {hit.therapeuticGroup && (
                  <span className="badge badge-accent">{hit.therapeuticGroup}</span>
                )}
                {hit.drugClass && !sameGroupAndClass && (
                  <span className="badge badge-accent-2">{hit.drugClass}</span>
                )}
                {hit.state !== 'published' && (
                  <span className="badge badge-medium">{hit.state.replace(/_/g, ' ')}</span>
                )}
                {hit.matchKind === 'fuzzy' && <span className="badge">≈</span>}
              </span>
            </Link>

            {/* A fixed-size column rather than the old checkbox-plus-word —
                consistently placed whether the row above it is one line or
                three, and the full meaning still reaches a screen reader
                through the label. */}
            <div className="result-compare">
              <label title={isSelected ? t.compareRemove : t.compareAdd}>
                <input
                  type="checkbox"
                  checked={isSelected}
                  disabled={atLimit}
                  onChange={() => onToggleCompare(hit.slug)}
                />
                <span className="sr-only">
                  {isSelected ? t.compareRemove : t.compareAdd}: {name}
                </span>
              </label>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
