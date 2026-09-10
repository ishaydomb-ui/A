import { useEffect, useRef, useState } from 'react';
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom';
import { useI18n } from '../i18n.ts';
import { useAuth } from '../lib/auth.tsx';
import { api } from '../lib/api.ts';
import type { PublicSettings } from '../lib/types.ts';
import { LanguageToggle } from './LanguageToggle.tsx';

/** Account / language / sign-out. Rendered twice — inline on a wide screen,
    inside the overflow menu on a narrow one — never both at once; see the
    `.header-end` / `.user-menu` media query in styles.css. */
function UserActions({ onNavigate }: { onNavigate?: () => void }) {
  const { session, signOut } = useAuth();
  const { t } = useI18n();
  return (
    <>
      <LanguageToggle />
      <Link to="/account" className="btn btn-sm btn-secondary" onClick={onNavigate}>
        {session?.user.displayName ?? t.navAccount}
      </Link>
      <button type="button" className="btn btn-sm" onClick={() => void signOut()}>
        {t.navSignOut}
      </button>
    </>
  );
}

export function Layout() {
  const { t } = useI18n();
  const { session, can } = useAuth();
  const location = useLocation();
  const [settings, setSettings] = useState<PublicSettings | null>(null);
  const [noticeOpen, setNoticeOpen] = useState(false);
  const navRef = useRef<HTMLElement>(null);
  const menuRef = useRef<HTMLDetailsElement>(null);

  useEffect(() => {
    api.get<PublicSettings>('/api/settings/public').then(setSettings).catch(() => undefined);
  }, []);

  // Moving focus to the main region on navigation is what lets a keyboard or
  // screen-reader user notice the page changed in a single-page app.
  //
  // Focus moves only when the route actually changes. On a fresh page load the
  // browser's own tab order must apply, so the first Tab reaches the skip link
  // rather than starting past it. Comparing the pathname rather than counting
  // renders is what makes this correct under StrictMode, which deliberately
  // invokes effects twice in development.
  const lastPath = useRef(location.pathname);
  useEffect(() => {
    if (lastPath.current === location.pathname) return;
    lastPath.current = location.pathname;
    document.getElementById('main-content')?.focus({ preventScroll: true });
    // The active tab can be scrolled out of view in the horizontally
    // scrolling nav; bring it back rather than leaving it hidden.
    navRef.current
      ?.querySelector('[aria-current="page"]')
      ?.scrollIntoView({ inline: 'nearest', block: 'nearest' });
    // Navigating away closes the overflow menu, the same way a normal page
    // link would.
    if (menuRef.current) menuRef.current.open = false;
  }, [location.pathname]);

  // <details> has no built-in "click elsewhere to close" behaviour; a menu
  // that only Escape or its own trigger can dismiss reads as stuck open.
  useEffect(() => {
    function onDocumentClick(event: MouseEvent) {
      const menu = menuRef.current;
      if (menu?.open && !menu.contains(event.target as Node)) menu.open = false;
    }
    document.addEventListener('click', onDocumentClick);
    return () => document.removeEventListener('click', onDocumentClick);
  }, []);

  const institution = settings?.settings['institution_name'];
  const contactEmail = settings?.settings['contact_email'];

  return (
    <div className="app">
      <a className="skip-link" href="#main-content">
        {t.skipToContent}
      </a>

      <header className="app-header">
        <div className="app-header-inner">
          <Link to="/" className="brand">
            {t.appName}
          </Link>

          <nav className="app-nav" aria-label={t.mainNavigation} ref={navRef}>
            <NavLink to="/" end>
              {t.navSearch}
            </NavLink>
            {can('review:read') && <NavLink to="/review">{t.navReview}</NavLink>}
            {can('import:create') && <NavLink to="/imports">{t.navImports}</NavLink>}
            {can('users:manage') && <NavLink to="/users">{t.navUsers}</NavLink>}
          </nav>

          {/* Visible on a wide screen; the overflow menu below takes over on
              a phone, where there is no room for three separate controls. */}
          <div className="header-end">
            <UserActions />
          </div>

          <details className="user-menu" ref={menuRef}>
            <summary aria-label={`${t.menu} — ${session?.user.displayName ?? t.navAccount}`}>
              {(session?.user.displayName ?? '?').trim().charAt(0).toUpperCase()}
            </summary>
            <div className="user-menu-panel">
              <UserActions onNavigate={() => menuRef.current && (menuRef.current.open = false)} />
            </div>
          </details>
        </div>
      </header>

      {/* While the catalogue is in evaluation this sits above every screen.
          Compact by default — a heading and one clamped line — because a
          warning that always fills the screen stops being read; "Details"
          opens the full text without ever making the warning disappear. */}
      {settings?.evaluationMode && settings.evaluationNotice && (
        <div className="evaluation-banner" role="status">
          <div className="evaluation-banner-inner">
            <svg
              className="evaluation-icon"
              viewBox="0 0 20 20"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              aria-hidden="true"
            >
              <path d="M10 2 1.5 17h17L10 2Z" strokeLinejoin="round" />
              <path d="M10 8v4" strokeLinecap="round" />
              <circle cx="10" cy="14.5" r="0.9" fill="currentColor" stroke="none" />
            </svg>
            <div className={`evaluation-text${noticeOpen ? '' : ' is-collapsed'}`}>
              <p>
                <strong>{t.evaluationHeading}</strong> {settings.evaluationNotice}
              </p>
            </div>
            <button
              type="button"
              className="evaluation-toggle"
              aria-expanded={noticeOpen}
              onClick={() => setNoticeOpen((v) => !v)}
            >
              {noticeOpen ? t.evaluationHide : t.evaluationDetails}
            </button>
          </div>
        </div>
      )}

      {/* tabIndex -1 makes the region focusable programmatically only. */}
      <main id="main-content" className="app-main" tabIndex={-1}>
        <Outlet />
      </main>

      <footer className="app-footer">
        <div className="app-footer-inner">
          <p style={{ margin: 0, flexBasis: '100%' }}>
            <strong>{t.disclaimerHeading}:</strong> {t.disclaimerShort} {t.noPatientData}
          </p>
          <nav aria-label={t.terms}>
            <Link to="/privacy">{t.privacyPolicy}</Link>
            <Link to="/terms">{t.terms}</Link>
            {contactEmail && !contactEmail.needsApproval ? (
              <a href={`mailto:${String(contactEmail.value)}`}>{t.contact}</a>
            ) : (
              <span className="muted">
                {t.contact}: {t.pendingApproval}
              </span>
            )}
          </nav>
          {institution?.needsApproval === false && (
            <span className="muted">{String(institution.value)}</span>
          )}
        </div>
      </footer>
    </div>
  );
}
