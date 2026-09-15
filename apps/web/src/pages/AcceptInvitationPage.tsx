import { useEffect, useState } from 'react';
import { Link, Navigate, useSearchParams } from 'react-router-dom';
import { useI18n } from '../i18n.ts';
import { useAuth } from '../lib/auth.tsx';
import { ApiError, api } from '../lib/api.ts';
import type { Session } from '../lib/types.ts';
import { AuthShell } from '../components/AuthShell.tsx';
import { MfaChallenge, type Challenge } from '../components/MfaChallenge.tsx';
import { Notice } from '../components/Notice.tsx';
import { MIN_PASSWORD_LENGTH, PasswordFields } from '../components/PasswordFields.tsx';
import { Spinner } from '../components/Spinner.tsx';

interface InvitationSummary {
  /** Null for an open link, which names nobody until it is opened. */
  email: string | null;
  displayName: string | null;
  role: string;
  expiresAt: string;
}

/**
 * The only route by which an account becomes usable. It requires a valid,
 * unexpired, unused invitation token issued by an administrator.
 */
export function AcceptInvitationPage() {
  const { t } = useI18n();
  const { session, setSession } = useAuth();
  const [params] = useSearchParams();
  const token = params.get('token') ?? '';

  const [invitation, setInvitation] = useState<InvitationSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [invalid, setInvalid] = useState(false);
  // Filled in by the recipient when the invitation names nobody.
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [challenge, setChallenge] = useState<Challenge | null>(null);

  useEffect(() => {
    if (!token) {
      setInvalid(true);
      setLoading(false);
      return;
    }
    api
      .get<InvitationSummary>(`/api/auth/invitations/${encodeURIComponent(token)}`)
      .then(setInvitation)
      .catch(() => setInvalid(true))
      .finally(() => setLoading(false));
  }, [token]);

  if (session && !challenge) return <Navigate to="/" replace />;

  const open = invitation !== null && invitation.email === null;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitted(true);
    if (password.length < MIN_PASSWORD_LENGTH || password !== confirm) return;
    if (open && (!email.trim() || !name.trim())) return;

    setBusy(true);
    setError(null);
    try {
      const result = await api.post<Session & { status: string; challengeToken?: string }>(
        '/api/auth/invitations/accept',
        open
          ? { token, password, email: email.trim(), displayName: name.trim() }
          : { token, password },
      );
      if (result.status === 'ok') setSession(result);
      else if (result.challengeToken) {
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

  if (loading) {
    return (
      <AuthShell title={t.acceptHeading}>
        <Spinner />
      </AuthShell>
    );
  }

  if (challenge) {
    return (
      <AuthShell title={t.acceptHeading}>
        <MfaChallenge challenge={challenge} onSuccess={setSession} />
      </AuthShell>
    );
  }

  if (invalid || !invitation) {
    return (
      <AuthShell title={t.acceptHeading}>
        <Notice tone="error">
          This invitation link is not valid, has already been used, or has expired. Ask an
          administrator for a new one.
        </Notice>
        <Link to="/sign-in" className="btn btn-secondary btn-block">
          {t.signIn}
        </Link>
      </AuthShell>
    );
  }

  return (
    <AuthShell title={t.acceptHeading}>
      <p className="muted small">{t.acceptHelp}</p>
      <dl className="field-list" style={{ marginBlockEnd: 16 }}>
        {invitation.email !== null && (
          <div className="field-row">
            <dt>{t.email}</dt>
            <dd>{invitation.email}</dd>
          </div>
        )}
        <div className="field-row">
          <dt>Role</dt>
          <dd>{invitation.role.replace(/_/g, ' ')}</dd>
        </div>
      </dl>

      <form onSubmit={submit} noValidate>
        {error && <Notice tone="error">{error}</Notice>}
        {open && (
          <>
            <div className="field">
              <label htmlFor="claim-name">{t.fullName}</label>
              <input
                id="claim-name"
                type="text"
                value={name}
                autoComplete="name"
                required
                maxLength={200}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="claim-email">{t.email}</label>
              <input
                id="claim-email"
                type="email"
                value={email}
                autoComplete="email"
                required
                maxLength={320}
                onChange={(e) => setEmail(e.target.value)}
              />
              <p className="hint">{t.acceptOwnEmailHint}</p>
            </div>
          </>
        )}
        <PasswordFields
          password={password}
          confirm={confirm}
          onPassword={setPassword}
          onConfirm={setConfirm}
          submitted={submitted}
        />
        <button type="submit" className="btn btn-primary btn-block" disabled={busy}>
          {busy ? t.loading : t.setPassword}
        </button>
      </form>
    </AuthShell>
  );
}
