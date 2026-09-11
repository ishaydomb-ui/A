import { useCallback, useEffect, useState } from 'react';
import { APPROVAL_STATUSES, FIELDS, getField } from '@med/shared';
import { useI18n } from '../i18n.ts';
import { ApiError, api } from '../lib/api.ts';
import { Notice } from './Notice.tsx';
import { Spinner } from './Spinner.tsx';

interface Suggestion {
  provider: string;
  externalId: string;
  title: string;
  url: string;
  description: string | null;
  publishedAt: string | null;
  jurisdiction: string;
}

interface Provider {
  name: string;
  label: string;
  describes: string;
  jurisdiction: string;
}

interface LookupResponse {
  genericName: string | null;
  providers: Provider[];
  suggestions: Suggestion[];
  unavailable: Array<{ provider: string; reason: string }>;
}

/**
 * Finds where a claim about this medication is documented, and lets a reviewer
 * attach one of the results as a citation.
 *
 * Everything here is a proposal. Nothing is attached until a person picks the
 * field, reads the document, states the jurisdiction the catalogue cares about
 * and chooses an approval status. A citation the system filled in by itself
 * would make an unchecked claim look sourced.
 */
export function SourceSuggestions({
  slug,
  versionId,
  onAttached,
}: {
  slug: string;
  versionId: string;
  onAttached(): void;
}) {
  const { t } = useI18n();
  const [data, setData] = useState<LookupResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [chosen, setChosen] = useState<Suggestion | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    api
      .get<LookupResponse>(`/api/medications/${encodeURIComponent(slug)}/source-suggestions`)
      .then(setData)
      .catch((err: unknown) => setError(err instanceof ApiError ? err.message : t.errorGeneric))
      .finally(() => setLoading(false));
  }, [slug, t]);

  useEffect(load, [load]);

  if (loading && !data) return <Spinner />;
  if (error) return <Notice tone="error">{error}</Notice>;
  if (!data) return null;

  return (
    <section aria-labelledby="sources-heading" className="stack">
      <div>
        <h2 id="sources-heading">{t.sourceLookupHeading}</h2>
        <p className="muted small">{t.sourceLookupIntro}</p>
      </div>

      {data.unavailable.map((entry) => (
        <Notice tone="warning" key={entry.provider}>
          {t.sourceLookupUnavailable(entry.provider)} {entry.reason}
        </Notice>
      ))}

      {data.suggestions.length === 0 && data.unavailable.length === 0 && (
        <p className="muted small">{t.sourceLookupNone}</p>
      )}

      {data.suggestions.length > 0 && (
        <ul className="stack-s" style={{ listStyle: 'none', padding: 0, margin: 0 }}>
          {data.suggestions.map((suggestion) => (
            <li className="card" key={`${suggestion.provider}-${suggestion.externalId}`}>
              <div className="row" style={{ alignItems: 'flex-start' }}>
                <div style={{ flex: 1, minWidth: 220 }}>
                  <strong>{suggestion.title}</strong>
                  {suggestion.description && (
                    <div className="small muted">{suggestion.description}</div>
                  )}
                  <div className="row small muted" style={{ gap: 8, marginBlockStart: 4 }}>
                    <span className="badge">{suggestion.provider}</span>
                    <span className="badge">{suggestion.jurisdiction}</span>
                    {suggestion.publishedAt && <span>{suggestion.publishedAt}</span>}
                  </div>
                </div>
                <div className="row" style={{ gap: 8 }}>
                  <a
                    className="btn btn-sm btn-secondary"
                    href={suggestion.url}
                    target="_blank"
                    rel="noreferrer noopener"
                  >
                    {t.sourceLookupOpen}
                  </a>
                  <button
                    type="button"
                    className="btn btn-sm btn-primary"
                    onClick={() => setChosen(suggestion)}
                  >
                    {t.sourceLookupUse}
                  </button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}

      {chosen && (
        <AttachForm
          suggestion={chosen}
          versionId={versionId}
          onCancel={() => setChosen(null)}
          onDone={() => {
            setChosen(null);
            onAttached();
          }}
        />
      )}
    </section>
  );
}

/**
 * The step where a person takes responsibility for the citation: which field
 * it supports, which jurisdiction it is being claimed for, and whether the use
 * is approved there. None of these are prefilled from the document, because
 * the document cannot answer the last two.
 */
function AttachForm({
  suggestion,
  versionId,
  onCancel,
  onDone,
}: {
  suggestion: Suggestion;
  versionId: string;
  onCancel(): void;
  onDone(): void;
}) {
  const { t, locale } = useI18n();
  const [fieldKey, setFieldKey] = useState('');
  const [page, setPage] = useState('');
  const [jurisdiction, setJurisdiction] = useState('');
  const [approvalStatus, setApprovalStatus] = useState('unknown');
  const [reviewedAt, setReviewedAt] = useState(new Date().toISOString().slice(0, 10));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post(`/api/medications/versions/${versionId}/citations`, {
        fieldKey,
        title: suggestion.title,
        url: suggestion.url,
        page: page || null,
        jurisdiction: jurisdiction || null,
        approvalStatus,
        reviewedAt,
        sourceProvider: suggestion.provider,
        externalId: suggestion.externalId,
      });
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t.errorGeneric);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card" onSubmit={submit}>
      <h3>{t.sourceLookupAttach}</h3>
      <p className="small muted">{suggestion.title}</p>

      {error && <Notice tone="error">{error}</Notice>}

      {/*
        The document governs one jurisdiction; the catalogue may be used in
        another. Saying so is the difference between a citation and a claim.
      */}
      <Notice tone="warning">
        {t.sourceLookupJurisdictionWarning(suggestion.jurisdiction)}
      </Notice>

      <div className="field">
        <label htmlFor="attach-field">{t.sourceLookupField}</label>
        <select
          id="attach-field"
          value={fieldKey}
          onChange={(e) => setFieldKey(e.target.value)}
          required
        >
          <option value="">—</option>
          {FIELDS.map((field) => (
            <option key={field.key} value={field.key}>
              {field.label[locale]}
            </option>
          ))}
        </select>
      </div>

      <div className="field">
        <label htmlFor="attach-page">{t.sourceLookupSection}</label>
        <input
          id="attach-page"
          type="text"
          value={page}
          onChange={(e) => setPage(e.target.value)}
          placeholder="4.2"
        />
        <p className="hint">{t.sourceLookupSectionHint}</p>
      </div>

      <div className="field">
        <label htmlFor="attach-jurisdiction">{t.jurisdiction}</label>
        <input
          id="attach-jurisdiction"
          type="text"
          value={jurisdiction}
          onChange={(e) => setJurisdiction(e.target.value)}
          placeholder="IL"
          required
        />
        <p className="hint">{t.sourceLookupJurisdictionHint}</p>
      </div>

      <div className="field">
        <label htmlFor="attach-status">{t.approvalStatus}</label>
        <select
          id="attach-status"
          value={approvalStatus}
          onChange={(e) => setApprovalStatus(e.target.value)}
        >
          {APPROVAL_STATUSES.map((status) => (
            <option key={status} value={status}>
              {status.replace(/_/g, ' ')}
            </option>
          ))}
        </select>
        <p className="hint">{t.sourceLookupStatusHint}</p>
      </div>

      <div className="field">
        <label htmlFor="attach-reviewed">{t.reviewDate}</label>
        <input
          id="attach-reviewed"
          type="date"
          value={reviewedAt}
          onChange={(e) => setReviewedAt(e.target.value)}
          required
        />
      </div>

      <div className="row">
        <button type="submit" className="btn btn-primary" disabled={busy || !fieldKey}>
          {busy ? t.loading : t.sourceLookupConfirm}
        </button>
        <button type="button" className="btn btn-secondary" onClick={onCancel}>
          {t.cancel}
        </button>
      </div>
      <p className="hint">{getField(fieldKey)?.label[locale] ? t.sourceLookupResponsibility : ''}</p>
    </form>
  );
}

