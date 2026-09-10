import { Link } from 'react-router-dom';
import { useI18n } from '../i18n.ts';
import type { SearchHit } from '../lib/types.ts';
import { Highlight } from './Highlight.tsx';

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
                {hit.therapeuticGroup && <span>{hit.therapeuticGroup}</span>}
                {hit.drugClass && <span>· {hit.drugClass}</span>}
                {hit.state !== 'published' && (
                  <span className="badge badge-medium">{hit.state.replace(/_/g, ' ')}</span>
                )}
                {hit.matchKind === 'fuzzy' && <span className="badge">≈</span>}
              </span>
            </Link>

            <div className="result-compare">
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={isSelected}
                  disabled={atLimit}
                  onChange={() => onToggleCompare(hit.slug)}
                />
                {/* The visible word "Compare" would repeat on every row, so the
                    accessible name carries the medication it belongs to. */}
                <span className="sr-only">
                  {isSelected ? t.compareRemove : t.compareAdd}: {name}
                </span>
                <span aria-hidden="true" className="small muted">
                  {t.compare}
                </span>
              </label>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
