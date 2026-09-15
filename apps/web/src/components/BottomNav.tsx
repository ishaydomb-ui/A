import { NavLink } from 'react-router-dom';
import { useI18n } from '../i18n.ts';
import { useCompareSelection } from '../lib/compareSelection.ts';

/**
 * The primary mobile navigation — four destinations everyone has, regardless
 * of role, so unlike the old top nav this needs no capability check. Visible
 * only under the header's own mobile breakpoint (see .bottom-nav in
 * styles.css); the equivalent desktop nav lives in Layout's .app-nav.
 */
export function BottomNav() {
  const { t } = useI18n();
  const { selected } = useCompareSelection();
  const compareHref = selected.length > 0 ? `/compare?slugs=${selected.map(encodeURIComponent).join(',')}` : '/compare';

  return (
    <nav className="bottom-nav" aria-label={t.mainNavigation}>
      <NavLink to="/" end className="bottom-nav-item">
        <ExploreIcon />
        <span>{t.navExplore}</span>
      </NavLink>
      <NavLink to="/catalogue" className="bottom-nav-item">
        <SearchIcon />
        <span>{t.navCatalogue}</span>
      </NavLink>
      <NavLink to={compareHref} className="bottom-nav-item">
        <span className="bottom-nav-icon-wrap">
          <CompareIcon />
          {selected.length > 0 && <span className="bottom-nav-badge">{selected.length}</span>}
        </span>
        <span>{t.compare}</span>
      </NavLink>
      <NavLink to="/saved" className="bottom-nav-item">
        <SavedIcon />
        <span>{t.navSaved}</span>
      </NavLink>
    </nav>
  );
}

function ExploreIcon() {
  return (
    <svg viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
      <circle cx="10" cy="10" r="7.5" />
      <path d="m13 7-4.5 2.5L6 14l4.5-2.5L13 7Z" strokeLinejoin="round" />
    </svg>
  );
}
function SearchIcon() {
  return (
    <svg viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
      <circle cx="8.7" cy="8.7" r="6.2" />
      <path d="m17 17-3.6-3.6" strokeLinecap="round" />
    </svg>
  );
}
function CompareIcon() {
  return (
    <svg viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
      <rect x="2.5" y="3" width="6" height="14" rx="1.2" />
      <rect x="11.5" y="3" width="6" height="14" rx="1.2" />
    </svg>
  );
}
function SavedIcon() {
  return (
    <svg viewBox="0 0 16 20" width="17" height="20" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true">
      <path d="M1.5 1.5h13v17l-6.5-4.5-6.5 4.5v-17Z" strokeLinejoin="round" />
    </svg>
  );
}
