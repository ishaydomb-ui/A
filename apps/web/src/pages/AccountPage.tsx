import { useId, useState } from 'react';
import { useI18n } from '../i18n.ts';
import { useAuth } from '../lib/auth.tsx';
import { ApiError, api } from '../lib/api.ts';
import { Notice } from '../components/Notice.tsx';
import { MIN_PASSWORD_LENGTH, PasswordFields } from '../components/PasswordFields.tsx';

export function AccountPage() {
  const { t } = useI18n();
  const { session, signOut } = useAuth();
  const currentId = useId();

  const [current, setCurrent] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitted(true);
    if (password.length < MIN_PASSWORD_LENGTH || password !== confirm) return;

    setBusy(true);
    setError(null);
    try {
      await api.post('/api/auth/password/change', { currentPassword: current, newPassword: password });
      setDone(true);
      setCurrent('');
      setPassword('');
      setConfirm('');
      setSubmitted(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t.errorGeneric);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1>{t.navAccount}</h1>

      <dl className="field-list" style={{ marginBlockEnd: 24 }}>
        <div className="field-row">
          <dt>{t.signedInAs}</dt>
          <dd>
            {session?.user.displayName} · {session?.user.email}
          </dd>
        </div>
        <div className="field-row">
          <dt>Role</dt>
          <dd>{session?.user.role.replace(/_/g, ' ')}</dd>
        </div>
        <div className="field-row">
          <dt>Two-factor authentication</dt>
          <dd>
            {session?.user.mfaEnabled ? (
              <span className="badge badge-success">Enabled</span>
            ) : (
              <span className="badge">Not enabled</span>
            )}
          </dd>
        </div>
      </dl>

      <section className="card" aria-labelledby="password-heading" style={{ maxWidth: 480 }}>
        <h2 id="password-heading">{t.changePassword}</h2>
        {done && <Notice tone="success">{t.passwordChanged}</Notice>}
        {error && <Notice tone="error">{error}</Notice>}

        <form onSubmit={submit} noValidate>
          <div className="field">
            <label htmlFor={currentId}>{t.currentPassword}</label>
            <input
              id={currentId}
              type="password"
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
              required
              autoComplete="current-password"
            />
          </div>
          <PasswordFields
            password={password}
            confirm={confirm}
            onPassword={setPassword}
            onConfirm={setConfirm}
            submitted={submitted}
          />
          <p className="hint">Changing your password signs you out of every other device.</p>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? t.loading : t.changePassword}
          </button>
        </form>
      </section>

      <p style={{ marginBlockStart: 24 }}>
        <button type="button" className="btn btn-secondary" onClick={() => void signOut()}>
          {t.navSignOut}
        </button>
      </p>
    </>
  );
}
