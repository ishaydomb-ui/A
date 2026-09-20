import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
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
  lastActiveAt: string | null;
  signIns7d: number;
  signIns30d: number;
  searches30d: number;
  views30d: number;
  emptySearches30d: number;
  failedLogins30d: number;
  lockouts30d: number;
  device: string | null;
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
  gaps: { term: string; times: number; people: number }[];
  popular: { slug: string; views: number; people: number }[];
  people: Person[];
}

/**
 * Who has an account, who is coming back, and where people get stuck.
 *
 * Per person this reports volume — sign-ins, searches, records opened,
 * failed sign-ins — rather than a transcript of what they read. The counts
 * answer "is this working for them", which is the question a pilot needs
 * answered; a list of every drug a colleague looked up answers a different
 * one nobody asked.
 *
 * Searches that found nothing are the exception, and are shown with their
 * terms: there the term is the point, because it names content the
 * catalogue does not have.
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
      <p className="quiet usage-scope">{t.usageLast30d}</p>
      <div className="usage-scroll">
        <table className="usage-table">
          <thead>
            <tr>
              <th scope="col">{t.usageName}</th>
              <th scope="col">{t.usageLastActive}</th>
              <th scope="col" className="num">
                {t.usageSignIns30d}
              </th>
              <th scope="col" className="num">
                {t.usageSearches30d}
              </th>
              <th scope="col" className="num">
                {t.usageViews30d}
              </th>
              <th scope="col" className="num">
                {t.usageEmptySearches30d}
              </th>
              <th scope="col">{t.usageTrouble}</th>
              <th scope="col">{t.usageDevice}</th>
            </tr>
          </thead>
          <tbody>
            {usage.people.map((p) => (
              <tr key={p.id}>
                <th scope="row">
                  <span className="usage-person">{p.displayName}</span>
                  {p.status !== 'active' && <span className="badge badge-medium">{p.status}</span>}
                  <span className="usage-person-meta">
                    {p.email} · {p.role.replace(/_/g, ' ')}
                  </span>
                </th>
                <td>
                  {fmt(p.lastActiveAt)}
                  {/* Only worth a second line when the two fall on different
                      days — otherwise it repeats what is already above it. */}
                  {fmt(p.lastSignInAt) !== fmt(p.lastActiveAt) && (
                    <span className="usage-person-meta">
                      {t.usageLastSeen}: {fmt(p.lastSignInAt)}
                    </span>
                  )}
                </td>
                <td className="num">{p.signIns30d || '—'}</td>
                <td className="num">{p.searches30d || '—'}</td>
                <td className="num">{p.views30d || '—'}</td>
                <td className="num">{p.emptySearches30d || '—'}</td>
                <td>
                  {p.failedLogins30d === 0 && p.lockouts30d === 0 ? (
                    '—'
                  ) : (
                    <span className="usage-trouble">
                      {p.failedLogins30d > 0 &&
                        t.usageFailedSignIns.replace('{n}', String(p.failedLogins30d))}
                      {p.lockouts30d > 0 && (
                        <span className="badge badge-high">
                          {t.usageLockouts.replace('{n}', String(p.lockouts30d))}
                        </span>
                      )}
                    </span>
                  )}
                </td>
                <td className="quiet">{p.device ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2>{t.usageGapsTitle}</h2>
      <p className="quiet">{t.usageGapsIntro}</p>
      {usage.gaps.length === 0 ? (
        <p className="quiet">{t.usageGapsNone}</p>
      ) : (
        <table className="usage-table">
          <thead>
            <tr>
              <th scope="col">{t.usageTerm}</th>
              <th scope="col" className="num">
                {t.usageTimes}
              </th>
              <th scope="col" className="num">
                {t.usagePeopleCount}
              </th>
            </tr>
          </thead>
          <tbody>
            {usage.gaps.map((g) => (
              <tr key={g.term}>
                <th scope="row" className="usage-term">
                  {g.term}
                </th>
                <td className="num">{g.times}</td>
                <td className="num">{g.people}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h2>{t.usagePopularTitle}</h2>
      {usage.popular.length === 0 ? (
        <p className="quiet">{t.usagePopularNone}</p>
      ) : (
        <table className="usage-table">
          <thead>
            <tr>
              <th scope="col">{t.usageRecord}</th>
              <th scope="col" className="num">
                {t.usageOpened}
              </th>
              <th scope="col" className="num">
                {t.usagePeopleCount}
              </th>
            </tr>
          </thead>
          <tbody>
            {usage.popular.map((r) => (
              <tr key={r.slug}>
                <th scope="row">
                  <Link to={`/medications/${r.slug}`}>{r.slug.replace(/-/g, ' ')}</Link>
                </th>
                <td className="num">{r.views}</td>
                <td className="num">{r.people}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <p className="quiet usage-footnote">{t.usageNotTracked}</p>
    </article>
  );
}
