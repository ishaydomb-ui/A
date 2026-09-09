import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useI18n } from '../i18n.ts';
import { LanguageToggle } from './LanguageToggle.tsx';

export function AuthShell({ title, children }: { title: string; children: ReactNode }) {
  const { t } = useI18n();

  return (
    <div className="auth-shell">
      <main className="auth-card">
        <div className="row" style={{ marginBlockEnd: 16 }}>
          <strong>{t.appName}</strong>
          <span className="spacer" />
          <LanguageToggle />
        </div>

        <h1>{title}</h1>
        {children}
      </main>

      <footer className="app-footer" style={{ background: 'transparent', border: 0, marginBlockStart: 16 }}>
        <div className="app-footer-inner" style={{ justifyContent: 'center' }}>
          <nav aria-label={t.terms}>
            <Link to="/privacy">{t.privacyPolicy}</Link>
            <Link to="/terms">{t.terms}</Link>
          </nav>
        </div>
      </footer>
    </div>
  );
}
