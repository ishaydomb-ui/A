import { useId, useState } from 'react';
import { Link, Navigate, useSearchParams } from 'react-router-dom';
import { useI18n } from '../i18n.ts';
import { useAuth } from '../lib/auth.tsx';
import { ApiError, api } from '../lib/api.ts';
import type { Session } from '../lib/types.ts';
import { AuthShell } from '../components/AuthShell.tsx';
import { MfaChallenge, type Challenge } from '../components/MfaChallenge.tsx';
import { Notice } from '../components/Notice.tsx';

export function SignInPage() {
  const { t } = useI18n();
  const { session, setSession } = useAuth();
  const [params] = useSearchParams();
  const emailId = useId();
  const passwordId = useId();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [challenge, setChallenge] = useState<Challenge | null>(null);

  const next = params.get('next');
  if (session) return <Navigate to={next && next.startsWith('/') ? next : '/'} replace />;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await api.post<
        Session & { status: string; challengeToken?: string }
      >('/api/auth/login', { email: email.trim(), password });

      if (result.status === 'ok') {
        setSession(result);
      } else if (result.challengeToken) {
        setChallenge({
          status: result.status as Challenge['status'],
          challengeToken: result.challengeToken,
        });
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t.errorGeneric);
    } finally {
      setBusy(false);
    }
  }

  if (challenge) {
    return (
      <AuthShell title={t.appName}>
        <MfaChallenge challenge={challenge} onSuccess={setSession} />
      </AuthShell>
    );
  }

  return (
    <AuthShell title={t.signInHeading}>
      <form onSubmit={submit} noValidate>
        {error && <Notice tone="error">{error}</Notice>}

        <div className="field">
          <label htmlFor={emailId}>{t.email}</label>
          <input
            id={emailId}
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoComplete="username"
            autoFocus
            inputMode="email"
          />
        </div>

        <div className="field">
          <label htmlFor={passwordId}>{t.password}</label>
          <input
            id={passwordId}
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            autoComplete="current-password"
          />
        </div>

        <button type="submit" className="btn btn-primary btn-block" disabled={busy}>
          {busy ? t.loading : t.signIn}
        </button>
      </form>

      <div className="auth-footer-links">
        <Link to="/forgot-password">{t.forgotPassword}</Link>
        <p className="muted small" style={{ margin: 0 }}>
          {t.accessByInvitation}
        </p>
      </div>
    </AuthShell>
  );
}
