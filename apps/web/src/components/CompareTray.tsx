import { useNavigate } from 'react-router-dom';
import { useI18n } from '../i18n.ts';

export function CompareTray({
  selected,
  onRemove,
  onClear,
  maxCompare,
}: {
  selected: string[];
  onRemove(slug: string): void;
  onClear(): void;
  maxCompare: number;
}) {
  const { t } = useI18n();
  const navigate = useNavigate();

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
