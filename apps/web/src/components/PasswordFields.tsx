import { useId } from 'react';
import { useI18n } from '../i18n.ts';

export const MIN_PASSWORD_LENGTH = 12;

/**
 * New-password entry with confirmation. Validation messages are tied to the
 * inputs with aria-describedby and aria-invalid so they are announced rather
 * than only shown.
 */
export function PasswordFields({
  password,
  confirm,
  onPassword,
  onConfirm,
  submitted,
}: {
  password: string;
  confirm: string;
  onPassword(v: string): void;
  onConfirm(v: string): void;
  submitted: boolean;
}) {
  const { t } = useI18n();
  const passwordId = useId();
  const confirmId = useId();
  const hintId = useId();
  const errorId = useId();

  const tooShort = password.length > 0 && password.length < MIN_PASSWORD_LENGTH;
  const mismatch = confirm.length > 0 && password !== confirm;
  const showShort = tooShort || (submitted && password.length < MIN_PASSWORD_LENGTH);

  return (
    <>
      <div className="field">
        <label htmlFor={passwordId}>{t.newPassword}</label>
        <input
          id={passwordId}
          type="password"
          value={password}
          onChange={(e) => onPassword(e.target.value)}
          required
          minLength={MIN_PASSWORD_LENGTH}
          autoComplete="new-password"
          aria-describedby={showShort ? `${hintId} ${errorId}` : hintId}
          aria-invalid={showShort || undefined}
        />
        <p className="hint" id={hintId}>
          {t.passwordMinLength}
        </p>
        {showShort && (
          <p className="error-text" id={errorId}>
            {t.passwordMinLength}
          </p>
        )}
      </div>

      <div className="field">
        <label htmlFor={confirmId}>{t.confirmPassword}</label>
        <input
          id={confirmId}
          type="password"
          value={confirm}
          onChange={(e) => onConfirm(e.target.value)}
          required
          autoComplete="new-password"
          aria-describedby={mismatch ? `${confirmId}-error` : undefined}
          aria-invalid={mismatch || undefined}
        />
        {mismatch && (
          <p className="error-text" id={`${confirmId}-error`}>
            {t.passwordsDoNotMatch}
          </p>
        )}
      </div>
    </>
  );
}
