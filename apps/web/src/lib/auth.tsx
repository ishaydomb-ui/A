import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import type { Capability } from '@med/shared';
import { api, onUnauthorized } from './api.ts';
import type { Session } from './types.ts';

interface AuthValue {
  session: Session | null;
  loading: boolean;
  can(capability: Capability): boolean;
  setSession(session: Session | null): void;
  refresh(): Promise<void>;
  signOut(): Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const me = await api.get<Session>('/api/auth/me');
      setSession(me);
    } catch {
      // Not signed in, or the session has expired: both mean "no session".
      setSession(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    // Any 401 anywhere in the app drops the session, so the UI cannot keep
    // showing controls the server would refuse.
    return onUnauthorized(() => setSession(null));
  }, [refresh]);

  const signOut = useCallback(async () => {
    try {
      await api.post('/api/auth/logout');
    } finally {
      setSession(null);
    }
  }, []);

  const can = useCallback(
    (capability: Capability) => session?.capabilities.includes(capability) ?? false,
    [session],
  );

  const value = useMemo<AuthValue>(
    () => ({ session, loading, can, setSession, refresh, signOut }),
    [session, loading, can, refresh, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error('useAuth must be used inside AuthProvider');
  return value;
}
