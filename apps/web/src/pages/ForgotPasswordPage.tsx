import { useId, useState } from 'react';
import { Link } from 'react-router-dom';
import { useI18n } from '../i18n.ts';
import { ApiError, api } from '../lib/api.ts';
import { AuthShell } from '../components/AuthShell.tsx';
import { Notice } from '../components/Notice.tsx';

export function ForgotPasswordPage() {
  const { t } = useI18n();
  const emailId = useId();
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await api.post<{ message: string }>('/api/auth/password/forgot', {
        email: email.trim(),
      });
      // The server answers identically whether or not the address exists, and
      // so does this screen.
      setSent(true);
      void result;
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t.errorGeneric);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell title={t.forgotHeading}>
      {sent ? (
        <>
          <Notice tone="success">
            If that address has an account, a reset link has been sent. The link can be used once
            and expires shortly.
          </Notice>
          <Link to="/sign-in" className="btn btn-secondary btn-block">
            {t.signIn}
          </Link>
        </>
      ) : (
        <form onSubmit={submit} noValidate>
          <p className="muted small">{t.forgotHelp}</p>
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
            />
          </div>

          <button type="submit" className="btn btn-primary btn-block" disabled={busy}>
            {busy ? t.loading : t.sendResetLink}
          </button>

          <div className="auth-footer-links">
            <Link to="/sign-in">{t.signIn}</Link>
          </div>
        </form>
      )}
    </AuthShell>
  );
}
