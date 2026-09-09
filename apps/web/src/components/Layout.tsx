import { useEffect, useState } from 'react';
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom';
import { useI18n } from '../i18n.ts';
import { useAuth } from '../lib/auth.tsx';
import { api } from '../lib/api.ts';
import type { PublicSettings } from '../lib/types.ts';
import { LanguageToggle } from './LanguageToggle.tsx';

export function Layout() {
  const { t } = useI18n();
  const { session, can, signOut } = useAuth();
  const location = useLocation();
  const [settings, setSettings] = useState<PublicSettings | null>(null);

  useEffect(() => {
    api.get<PublicSettings>('/api/settings/public').then(setSettings).catch(() => undefined);
  }, []);

  // Moving focus to the main heading on navigation is what lets a keyboard or
  // screen-reader user notice the page changed in a single-page app.
  useEffect(() => {
    const main = document.getElementById('main-content');
    main?.focus({ preventScroll: true });
  }, [location.pathname]);

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

          <nav className="app-nav" aria-label={t.mainNavigation}>
            <NavLink to="/" end>
              {t.navSearch}
            </NavLink>
            {can('review:read') && <NavLink to="/review">{t.navReview}</NavLink>}
            {can('import:create') && <NavLink to="/imports">{t.navImports}</NavLink>}
            {can('users:manage') && <NavLink to="/users">{t.navUsers}</NavLink>}
          </nav>

          <div className="header-end">
            <LanguageToggle />
            <Link to="/account" className="btn btn-sm btn-secondary">
              {session?.user.displayName ?? t.navAccount}
            </Link>
            <button type="button" className="btn btn-sm" onClick={() => void signOut()}>
              {t.navSignOut}
            </button>
          </div>
        </div>
      </header>

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
