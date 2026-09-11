import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useI18n } from '../i18n.ts';
import { api } from '../lib/api.ts';
import type { PublicSettings } from '../lib/types.ts';
import { Notice } from '../components/Notice.tsx';
import { LanguageToggle } from '../components/LanguageToggle.tsx';
import { ThemeToggle } from '../components/ThemeToggle.tsx';

/**
 * Privacy Policy and Terms of Use.
 *
 * The binding wording is the system owner's to approve, so nothing here
 * pretends to be a finished legal document. Until an approved text is stored
 * in settings, the page shows the factual description of what the system
 * actually does — which is what a lawyer needs in order to draft the real
 * text — clearly labelled as a draft awaiting approval.
 */
export function LegalPage({ document: which }: { document: 'privacy' | 'terms' }) {
  const { t, locale } = useI18n();
  const [settings, setSettings] = useState<PublicSettings | null>(null);

  useEffect(() => {
    api.get<PublicSettings>('/api/settings/public').then(setSettings).catch(() => undefined);
  }, []);

  const key = which === 'privacy' ? 'legal.privacy_policy' : 'legal.terms';
  const stored = settings?.settings[key];
  const approved = stored && !stored.needsApproval ? String(stored.value) : null;
  const title = which === 'privacy' ? t.privacyPolicy : t.terms;

  return (
    <div className="auth-shell" style={{ justifyContent: 'flex-start', paddingBlock: 32 }}>
      <main className="auth-card" style={{ maxInlineSize: 760 }}>
        <div className="row" style={{ marginBlockEnd: 16 }}>
          <Link to="/">{t.appName}</Link>
          <span className="spacer" />
          <LanguageToggle />
          <ThemeToggle />
        </div>

        <h1>{title}</h1>

        {approved ? (
          <div style={{ whiteSpace: 'pre-wrap' }}>{approved}</div>
        ) : (
          <>
            <Notice tone="warning" title={t.disclaimerHeading}>
              <p>
                {locale === 'he'
                  ? 'זהו תיאור עובדתי של התנהגות המערכת, ולא נוסח משפטי סופי. הנוסח המחייב ממתין לאישור בעל המערכת.'
                  : 'This is a factual description of how the system behaves, not final legal wording. The binding text is awaiting the system owner’s approval.'}
              </p>
            </Notice>

            {which === 'privacy' ? <PrivacyDraft /> : <TermsDraft />}
          </>
        )}

        <hr style={{ margin: '24px 0', border: 0, borderBlockStart: '1px solid var(--border)' }} />
        <p className="small muted">
          {which === 'privacy' ? (
            <Link to="/terms">{t.terms}</Link>
          ) : (
            <Link to="/privacy">{t.privacyPolicy}</Link>
          )}
        </p>
      </main>
    </div>
  );
}

function PrivacyDraft() {
  return (
    <>
      <h2>What this system stores</h2>
      <ul>
        <li>
          <strong>Account details</strong> — your name, email address and role. Your password is
          stored only as an Argon2id hash and cannot be recovered from it.
        </li>
        <li>
          <strong>Sign-in records</strong> — the time, IP address and browser user-agent of each
          sign-in, and of security events such as failed attempts and password changes.
        </li>
        <li>
          <strong>Editorial activity</strong> — for anyone who edits, reviews or publishes content:
          which record changed, what changed, when, and who did it.
        </li>
      </ul>

      <h2>What it does not store</h2>
      <p>
        <strong>No patient data of any kind.</strong> There is no field anywhere in this system for
        patient identifiers, and search terms are not retained against your account. Do not type
        patient-identifiable information into any input, including the search box.
      </p>

      <h2>Why it is stored</h2>
      <p>
        Account details are needed to give you access. Sign-in records and editorial activity exist
        so that changes to clinical content are attributable and auditable, which is a requirement
        of the review-and-publish process rather than a form of monitoring.
      </p>

      <h2>Who can see it</h2>
      <p>
        Your account details are visible to administrators. The audit log is readable only by
        administrators. Clinical content is visible to every signed-in clinician once published.
      </p>

      <h2>Retention</h2>
      <p>
        Expired sessions are deleted automatically. The audit log and revision history are retained
        as an append-only record and cannot be edited or deleted, including by administrators.
        Encrypted database backups are kept for a configured retention period and then destroyed.
      </p>

      <h2>Your choices</h2>
      <p>
        You can change your password and sign out of all devices from your account page. To correct
        your name or role, or to have your account deactivated, contact an administrator.
      </p>
    </>
  );
}

function TermsDraft() {
  return (
    <>
      <h2>What this catalogue is</h2>
      <p>
        A professional reference aid for authorised clinicians. It summarises medication information
        drawn from source documents so that it can be searched and read quickly.
      </p>

      <h2>What it is not</h2>
      <p>
        <strong>
          It does not replace clinical judgement, and it is not a substitute for current, approved
          prescribing information.
        </strong>{' '}
        Always confirm dosing, contraindications and regulatory status against the current Summary of
        Product Characteristics or equivalent authority before prescribing. Records marked
        “Unvalidated” have not yet been reconciled with an authoritative source or approved by a
        clinical reviewer and must not be relied on.
      </p>

      <h2>Your account</h2>
      <p>
        Accounts are issued individually by an administrator. Do not share your account or your
        password. You are responsible for activity carried out under your account. Report a
        suspected compromise to an administrator immediately.
      </p>

      <h2>Acceptable use</h2>
      <ul>
        <li>Use the catalogue only for the professional purpose for which access was granted.</li>
        <li>Do not enter patient-identifiable information anywhere in the system.</li>
        <li>Do not bulk-extract, republish or redistribute the catalogue's contents.</li>
      </ul>

      <h2>Accuracy and reporting</h2>
      <p>
        Content is maintained through a review process, but errors are possible. If you believe a
        record is wrong, report it to an administrator or clinical reviewer so it can be
        investigated and corrected through the normal review workflow.
      </p>
    </>
  );
}
