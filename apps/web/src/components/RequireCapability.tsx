import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import type { Capability } from '@med/shared';
import { useI18n } from '../i18n.ts';
import { useAuth } from '../lib/auth.tsx';
import { Notice } from './Notice.tsx';

/**
 * Renders a page only for a role that holds the capability it needs.
 *
 * This is presentation, not protection — the API refuses the request either
 * way. Its purpose is that someone who follows a stale link is told plainly
 * that the page is not theirs, rather than being shown an editorial interface
 * whose every action would fail.
 */
export function RequireCapability({
  capability,
  children,
}: {
  capability: Capability;
  children: ReactNode;
}) {
  const { can } = useAuth();
  const { t } = useI18n();

  if (!can(capability)) {
    return (
      <>
        <h1>{t.errorForbidden}</h1>
        <Notice tone="error">
          <p>{t.errorForbidden}</p>
        </Notice>
        <Link to="/" className="btn btn-secondary">
          {t.navExplore}
        </Link>
      </>
    );
  }
  return <>{children}</>;
}
