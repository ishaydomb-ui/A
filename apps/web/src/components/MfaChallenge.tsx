import { useEffect, useId, useState } from 'react';
import { useI18n } from '../i18n.ts';
import { ApiError, api } from '../lib/api.ts';
import type { Session } from '../lib/types.ts';
import { Notice } from '../components/Notice.tsx';

export interface Challenge {
  status: 'mfa_required' | 'mfa_enrollment_required';
  challengeToken: string;
}

interface EnrollmentInfo {
  secret: string;
  otpauthUrl: string;
  qrDataUrl: string;
}

/**
 * The second authentication step.
 *
 * Handles both cases: entering a code for an existing authenticator, and
 * first-time enrolment for a role where MFA is mandatory — an administrator
 * receives no session at all until this completes.
 */
export function MfaChallenge({
  challenge,
  onSuccess,
}: {
  challenge: Challenge;
  onSuccess(session: Session): void;
}) {
  const { t } = useI18n();
  const codeId = useId();

  const [code, setCode] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [enrollment, setEnrollment] = useState<EnrollmentInfo | null>(null);
  const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null);
  const [pendingSession, setPendingSession] = useState<Session | null>(null);

  const enrolling = challenge.status === 'mfa_enrollment_required';

  useEffect(() => {
    if (!enrolling) return;
    api
      .post<EnrollmentInfo>('/api/auth/mfa/enroll/start', { challengeToken: challenge.challengeToken })
      .then(setEnrollment)
      .catch((err: ApiError) => setError(err.message));
  }, [enrolling, challenge.challengeToken]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const url = enrolling ? '/api/auth/mfa/enroll/complete' : '/api/auth/mfa/verify';
      const result = await api.post<Session & { recoveryCodes?: string[] }>(url, {
        challengeToken: challenge.challengeToken,
        code: code.trim(),
      });
      if (result.recoveryCodes?.length) {
        // Recovery codes are shown once; the session is held until the user
        // confirms they have saved them.
        setRecoveryCodes(result.recoveryCodes);
        setPendingSession(result);
      } else {
        onSuccess(result);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t.errorGeneric);
    } finally {
      setBusy(false);
    }
  }

  if (recoveryCodes && pendingSession) {
    return (
      <>
        <h2>{t.mfaRecoveryHeading}</h2>
        <Notice tone="warning">{t.mfaRecoveryHelp}</Notice>
        <ul className="chip-list mono" style={{ marginBlockEnd: 16 }}>
          {recoveryCodes.map((rc) => (
            <li key={rc}>{rc}</li>
          ))}
        </ul>
        <button type="button" className="btn btn-primary btn-block" onClick={() => onSuccess(pendingSession)}>
          {t.mfaRecoveryAcknowledge}
        </button>
      </>
    );
  }

  return (
    <form onSubmit={submit} noValidate>
      <h2>{enrolling ? t.mfaEnrollHeading : t.mfaHeading}</h2>
      <p className="muted small">{enrolling ? t.mfaEnrollHelp : t.mfaHelp}</p>

      {error && <Notice tone="error">{error}</Notice>}

      {enrolling && enrollment && (
        <div className="center" style={{ marginBlockEnd: 16 }}>
          {/*
            A QR code cannot be scanned from the same screen that shows it, so
            on a phone this link is the way in: iOS and Android hand an
            otpauth:// URL straight to an installed authenticator app, which
            adds the account without anything being typed.
          */}
          <a className="btn btn-primary btn-block" href={enrollment.otpauthUrl}>
            {t.mfaOpenAuthenticator}
          </a>
          <p className="small muted" style={{ marginBlock: 8 }}>
            {t.mfaOpenAuthenticatorHint}
          </p>

          <details style={{ marginBlockEnd: 12 }}>
            <summary className="small">{t.mfaOtherDevice}</summary>
            <img
              src={enrollment.qrDataUrl}
              width={180}
              height={180}
              alt="QR code for setting up your authenticator app"
              style={{ marginBlockStart: 12 }}
            />
            <p className="small muted" style={{ marginBlockEnd: 4 }}>
              {t.mfaSecretManual}
            </p>
            <p className="mono" style={{ wordBreak: 'break-all', userSelect: 'all' }}>
              {enrollment.secret}
            </p>
          </details>
        </div>
      )}

      <div className="field">
        <label htmlFor={codeId}>{t.mfaCode}</label>
        <input
          id={codeId}
          type="text"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          required
          inputMode="numeric"
          autoComplete="one-time-code"
          autoFocus
          maxLength={13}
          placeholder="000000"
          aria-describedby={`${codeId}-hint`}
          // A 6-digit TOTP or a hyphenated recovery code.
          pattern="[0-9A-Za-z\-\s]{6,13}"
        />
        {/* The setup key and the code look alike enough that pasting the key
            here is the obvious mistake to make. */}
        <p className="hint" id={`${codeId}-hint`}>
          {t.mfaCodeHint}
        </p>
      </div>

      <button type="submit" className="btn btn-primary btn-block" disabled={busy || code.trim().length < 6}>
        {busy ? t.loading : t.mfaVerify}
      </button>
    </form>
  );
}
