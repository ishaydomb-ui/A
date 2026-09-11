import type { ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from '../lib/auth.tsx';

/**
 * Client-side gate. It only controls what is rendered — every endpoint
 * re-checks authentication and capabilities on the server, so bypassing this
 * in the browser gains nothing.
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { session } = useAuth();
  const location = useLocation();

  if (!session) {
    const next = location.pathname + location.search;
    const target = next && next !== '/' ? `/sign-in?next=${encodeURIComponent(next)}` : '/sign-in';
    return <Navigate to={target} replace />;
  }
  return <>{children}</>;
}
