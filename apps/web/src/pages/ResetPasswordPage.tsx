import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useI18n } from '../i18n.ts';
import { ApiError, api } from '../lib/api.ts';
import { AuthShell } from '../components/AuthShell.tsx';
import { Notice } from '../components/Notice.tsx';
import { MIN_PASSWORD_LENGTH, PasswordFields } from '../components/PasswordFields.tsx';

export function ResetPasswordPage() {
  const { t } = useI18n();
  const [params] = useSearchParams();
  const token = params.get('token') ?? '';

  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [submitted, setSubmitted] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitted(true);
    if (password.length < MIN_PASSWORD_LENGTH || password !== confirm) return;

    setBusy(true);
    setError(null);
    try {
      await api.post('/api/auth/password/reset', { token, password });
      setDone(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t.errorGeneric);
    } finally {
      setBusy(false);
    }
  }

  if (!token) {
    return (
      <AuthShell title={t.resetHeading}>
        <Notice tone="error">This reset link is not valid.</Notice>
        <Link to="/forgot-password" className="btn btn-secondary btn-block">
          {t.sendResetLink}
        </Link>
      </AuthShell>
    );
  }

  return (
    <AuthShell title={t.resetHeading}>
      {done ? (
        <>
          <Notice tone="success">{t.passwordChanged}</Notice>
          <Link to="/sign-in" className="btn btn-primary btn-block">
            {t.signIn}
          </Link>
        </>
      ) : (
        <form onSubmit={submit} noValidate>
          {error && <Notice tone="error">{error}</Notice>}
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
      )}
    </AuthShell>
  );
}
