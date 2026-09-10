import { useNavigate } from 'react-router-dom';
import { MAX_COMPARE } from '@med/shared';
import { useI18n } from '../i18n.ts';
import { useCompareSelection } from '../lib/compareSelection.ts';

/**
 * Rendered once, globally (see Layout), so a selection made on the catalogue
 * stays visible while browsing Explore or a medication's own page. Hidden on
 * a phone below the BottomNav breakpoint — there, the bottom nav's own
 * Compare tab and its badge are the equivalent affordance; showing both
 * would just stack two bars.
 */
export function CompareTray() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { selected, remove: onRemove, clear: onClear } = useCompareSelection();
  const maxCompare = MAX_COMPARE;

  if (selected.length === 0) return null;

  return (
    <div className="compare-tray" role="region" aria-label={t.compareTrayLabel}>
      <span className="compare-tray-count">{t.compareSelectedCount(selected.length)}</span>
      <span className="small muted">
        {selected.length < 2 ? t.compareEmpty : t.compareLimit(maxCompare)}
      </span>
      <span className="spacer" />

      <button type="button" className="btn btn-sm btn-secondary" onClick={onClear}>
        {t.compareClear}
      </button>
      <button
        type="button"
        className="btn btn-sm btn-primary"
        disabled={selected.length < 2}
        onClick={() => navigate(`/compare?slugs=${selected.map(encodeURIComponent).join(',')}`)}
      >
        {t.compareOpen}
      </button>

      <div className="compare-chips">
        {selected.map((slug) => (
          <span className="chip" key={slug}>
            {slug}
            <button type="button" onClick={() => onRemove(slug)} aria-label={`${t.compareRemove}: ${slug}`}>
              ×
            </button>
          </span>
        ))}
      </div>
    </div>
  );
}
