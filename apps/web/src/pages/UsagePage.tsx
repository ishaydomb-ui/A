import { useEffect, useState } from 'react';
import { useI18n } from '../i18n.ts';
import { ApiError, api } from '../lib/api.ts';
import { Notice } from '../components/Notice.tsx';
import { Spinner } from '../components/Spinner.tsx';

interface Person {
  id: string;
  email: string;
  displayName: string;
  role: string;
  status: string;
  createdAt: string;
  lastSignInAt: string | null;
  signIns7d: number;
  signIns30d: number;
}

interface Usage {
  totals: {
    accounts: number;
    active: number;
    pending: number;
    signed_in_7d: number;
    never_signed_in: number;
  };
  daily: { day: string; sign_ins: number; people: number }[];
  people: Person[];
}

/**
 * Who has an account and who is signing in.
 *
 * Reading is not recorded anywhere in this system, so this cannot say what
 * anyone looked up — only whether they came back. That is the question a
 * pilot actually needs answered, and it is the one that can be answered
 * without watching colleagues work.
 */
export function UsagePage() {
  const { t, locale } = useI18n();
  const [usage, setUsage] = useState<Usage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    api
      .get<Usage>('/api/admin/usage', controller.signal)
      .then(setUsage)
      .catch((err: unknown) => {
        if ((err as Error).name === 'AbortError') return;
        setError(err instanceof ApiError ? err.message : t.errorGeneric);
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [t]);

  if (loading) return <Spinner />;
  if (error) return <Notice tone="error">{error}</Notice>;
  if (!usage) return null;

  const fmt = (iso: string | null): string => {
    if (!iso) return '—';
    const d = new Date(iso);
    const days = Math.floor((Date.now() - d.getTime()) / 86_400_000);
    const when = d.toLocaleDateString(locale === 'he' ? 'he-IL' : 'en-GB', {
      day: 'numeric',
      month: 'short',
    });
    if (days === 0) return t.usageToday;
    if (days === 1) return t.usageYesterday;
    return `${when} · ${t.usageDaysAgo.replace('{n}', String(days))}`;
  };

  const busiest = Math.max(1, ...usage.daily.map((d) => d.sign_ins));

  return (
    <article className="reading-width">
      <h1>{t.usageTitle}</h1>
      <p className="quiet">{t.usageIntro}</p>

      <section className="usage-tiles" aria-label={t.usageTitle}>
        <div className="usage-tile">
          <strong>{usage.totals.active}</strong>
          <span>{t.usageActiveAccounts}</span>
        </div>
        <div className="usage-tile">
          <strong>{usage.totals.signed_in_7d}</strong>
          <span>{t.usageSignedIn7d}</span>
        </div>
        <div className="usage-tile">
          <strong>{usage.totals.pending}</strong>
          <span>{t.usagePendingInvites}</span>
        </div>
        <div className="usage-tile">
          <strong>{usage.totals.never_signed_in}</strong>
          <span>{t.usageNeverSignedIn}</span>
        </div>
      </section>

      <h2>{t.usageLastFortnight}</h2>
      <ul className="usage-bars">
        {usage.daily.map((d) => (
          <li key={d.day}>
            <span className="usage-bar-day">{d.day.slice(5).replace('-', '/')}</span>
            <span className="usage-bar-track">
              <span
                className="usage-bar-fill"
                style={{ inlineSize: `${Math.round((d.sign_ins / busiest) * 100)}%` }}
              />
            </span>
            <span className="usage-bar-count">{d.sign_ins || ''}</span>
          </li>
        ))}
      </ul>

      <h2>{t.usagePeople}</h2>
      <table className="usage-table">
        <thead>
          <tr>
            <th scope="col">{t.usageName}</th>
            <th scope="col">{t.usageLastSeen}</th>
            <th scope="col" className="num">
              {t.usageSignIns30d}
            </th>
          </tr>
        </thead>
        <tbody>
          {usage.people.map((p) => (
            <tr key={p.id}>
              <th scope="row">
                {p.displayName}
                <span className="quiet"> · {p.role.replace(/_/g, ' ')}</span>
                {p.status !== 'active' && <span className="badge badge-medium">{p.status}</span>}
              </th>
              <td>{fmt(p.lastSignInAt)}</td>
              <td className="num">{p.signIns30d || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <p className="quiet usage-footnote">{t.usageNotTracked}</p>
    </article>
  );
}
