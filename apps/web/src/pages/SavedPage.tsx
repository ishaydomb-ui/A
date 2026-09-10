import { Link } from 'react-router-dom';
import { useI18n } from '../i18n.ts';
import { useSaved } from '../lib/saved.ts';

/**
 * A per-device bookmark list. Deliberately not backed by the catalogue's own
 * data — see lib/saved.ts — so it needs no loading, error or capability
 * handling of its own.
 */
export function SavedPage() {
  const { t } = useI18n();
  const { entries, remove } = useSaved();

  return (
    <>
      <h1>{t.navSaved}</h1>

      {entries.length === 0 ? (
        <div className="empty-state">
          <p>
            <strong>{t.savedEmpty}</strong>
          </p>
          <p className="small">{t.savedEmptyHint}</p>
        </div>
      ) : (
        <ul className="result-list">
          {entries.map((entry) => (
            <li key={entry.slug} className="result-row">
              <Link className="result-main" to={`/medications/${encodeURIComponent(entry.slug)}`}>
                <span className="result-name">
                  {entry.genericName}
                  {entry.tradeNames && <span className="result-trade">{entry.tradeNames}</span>}
                </span>
                {entry.therapeuticGroup && (
                  <span className="result-meta">
                    <span className="badge badge-accent">{entry.therapeuticGroup}</span>
                  </span>
                )}
              </Link>
              <button
                type="button"
                className="result-compare-remove"
                onClick={() => remove(entry.slug)}
                aria-label={`${t.bookmarkRemove}: ${entry.genericName}`}
                title={t.bookmarkRemove}
              >
                <span aria-hidden="true">×</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
