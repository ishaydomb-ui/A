import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { COMPARABLE_FIELDS, MAX_COMPARE } from '@med/shared';
import { useI18n } from '../i18n.ts';
import { api } from '../lib/api.ts';
import type { ResolvedFieldDto } from '../lib/types.ts';
import { FieldValueView } from '../components/FieldValue.tsx';
import { Notice } from '../components/Notice.tsx';
import { Spinner } from '../components/Spinner.tsx';

interface CompareEntry {
  slug: string;
  versionId: string;
  fields: Record<string, ResolvedFieldDto>;
}

/**
 * Side-by-side comparison.
 *
 * Only fields that are actually comparable appear: single-value clinical facts
 * such as dose range, onset and QTc. Long prose (indications, notes, side
 * effects) is deliberately excluded — in a column it is unreadable and invites
 * false equivalence between differently-worded sources.
 */
export function ComparePage() {
  const [params] = useSearchParams();
  const { t, locale } = useI18n();
  const slugs = (params.get('slugs') ?? '').split(',').filter(Boolean).slice(0, MAX_COMPARE);

  const [entries, setEntries] = useState<CompareEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (slugs.length < 2) {
      setLoading(false);
      return;
    }
    setLoading(true);
    api
      .post<{ medications: CompareEntry[] }>('/api/medications/compare', { slugs, locale })
      .then((res) => setEntries(res.medications))
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
    // The slug list is a stable string in the URL.
  }, [params, locale]);

  if (loading) return <Spinner />;

  if (slugs.length < 2) {
    return (
      <>
        <h1>{t.compareHeading}</h1>
        <Notice tone="info">{t.compareEmpty}</Notice>
        <Link to="/catalogue" className="btn btn-secondary">
          {t.backToResults}
        </Link>
      </>
    );
  }

  if (error) {
    return (
      <>
        <h1>{t.compareHeading}</h1>
        <Notice tone="error">{error}</Notice>
      </>
    );
  }

  return (
    <>
      <p>
        <Link to="/catalogue">← {t.backToResults}</Link>
      </p>
      <h1>{t.compareHeading}</h1>

      <div className="table-scroll">
        <table className="compare-table">
          <caption className="sr-only">
            {t.compareHeading}: {entries.map((e) => e.fields['generic_name']?.value.text ?? e.slug).join(', ')}
          </caption>
          <thead>
            <tr>
              <th scope="col">{t.compare}</th>
              {entries.map((entry) => (
                <th scope="col" key={entry.slug}>
                  <Link to={`/medications/${encodeURIComponent(entry.slug)}`}>
                    {entry.fields['generic_name']?.value.text ?? entry.slug}
                  </Link>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {COMPARABLE_FIELDS.filter((field) => field.key !== 'generic_name').map((field) => (
              <tr key={field.key}>
                <th scope="row">{field.label[locale]}</th>
                {entries.map((entry) => (
                  <td key={entry.slug}>
                    <FieldValueView fieldKey={field.key} field={entry.fields[field.key]} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <Notice tone="warning" title={t.disclaimerHeading}>
        <p>{t.disclaimerShort}</p>
      </Notice>
    </>
  );
}
