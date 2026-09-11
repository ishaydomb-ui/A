import { useEffect, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import {
  FIELD_GROUPS,
  FIELD_GROUP_LABELS,
  fieldsInGroup,
  profileFor,
  profileLabel,
  resolveProfileField,
} from '@med/shared';
import { useI18n } from '../i18n.ts';
import { useAuth } from '../lib/auth.tsx';
import { ApiError, api, qs } from '../lib/api.ts';
import { pushRecentlyViewed } from '../lib/recentlyViewed.ts';
import { useSaved } from '../lib/saved.ts';
import type { MedicationDetail } from '../lib/types.ts';
import { FieldValueView } from '../components/FieldValue.tsx';
import { SourceSuggestions } from '../components/SourceSuggestions.tsx';
import { Notice } from '../components/Notice.tsx';
import { Spinner } from '../components/Spinner.tsx';

/**
 * The full record, grouped by clinical area.
 *
 * Rendered as its own page rather than a modal: it is deep-linkable, works
 * with the back button, prints, and needs no focus trapping on a phone.
 */
export function MedicationPage() {
  const { slug = '' } = useParams();
  const [params] = useSearchParams();
  const { t, locale } = useI18n();
  const { can } = useAuth();
  const { isSaved, toggle: toggleSaved } = useSaved();

  const [detail, setDetail] = useState<MedicationDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const reload = () => setReloadKey((n) => n + 1);

  const wantsDraft = can('catalogue:read_unpublished') && params.get('draft') === '1';

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);

    api
      .get<MedicationDetail>(
        `/api/medications/${encodeURIComponent(slug)}${qs({ locale, draft: wantsDraft })}`,
        controller.signal,
      )
      .then(setDetail)
      .catch((err: unknown) => {
        if ((err as Error).name === 'AbortError') return;
        if (err instanceof ApiError && err.status === 404) setError(t.errorNotFound);
        else if (err instanceof ApiError && err.status === 403) setError(t.errorForbidden);
        else setError(err instanceof ApiError ? err.message : t.errorGeneric);
      })
      .finally(() => setLoading(false));

    return () => controller.abort();
  }, [slug, locale, wantsDraft, t, reloadKey]);

  // A medication reaches this page from Explore, the catalogue, a saved
  // list or a shared link — recording it here, once, covers all of them.
  useEffect(() => {
    if (!detail) return;
    pushRecentlyViewed({
      slug: detail.slug,
      genericName: detail.fields['generic_name']?.value.text ?? detail.slug,
      tradeNames: detail.fields['trade_names']?.value.text ?? null,
    });
  }, [detail]);

  if (loading) return <Spinner />;
  if (error) {
    return (
      <>
        <Notice tone="error">{error}</Notice>
        <Link to="/catalogue" className="btn btn-secondary">
          {t.backToResults}
        </Link>
      </>
    );
  }
  if (!detail) return null;

  const name = detail.fields['generic_name']?.value.text ?? detail.slug;
  const profile = profileFor(detail.fields['therapeutic_group']?.value.text);
  // Either the record's own validation status says so, or it was published
  // with clinical gates still outstanding.
  const unvalidated =
    detail.publishedUnvalidated || /unvalidated/i.test(detail.validationStatus);

  return (
    <article className="reading-width">
      <p>
        <Link to="/catalogue">← {t.backToResults}</Link>
      </p>

      <header className="detail-header">
        <div className="detail-title">
          <h1 style={{ margin: 0 }}>{name}</h1>
          {detail.state !== 'published' && (
            <span className="badge badge-medium">{detail.state.replace(/_/g, ' ')}</span>
          )}
        </div>
        <button
          type="button"
          className={`btn btn-sm btn-secondary bookmark-toggle${isSaved(detail.slug) ? ' is-saved' : ''}`}
          aria-pressed={isSaved(detail.slug)}
          onClick={() =>
            toggleSaved({
              slug: detail.slug,
              genericName: name,
              tradeNames: detail.fields['trade_names']?.value.text ?? null,
              therapeuticGroup: detail.fields['therapeutic_group']?.value.text ?? null,
            })
          }
        >
          <svg viewBox="0 0 16 20" width="14" height="17" aria-hidden="true">
            <path
              d="M1.5 1.5h13v17l-6.5-4.5-6.5 4.5v-17Z"
              fill={isSaved(detail.slug) ? 'currentColor' : 'none'}
              stroke="currentColor"
              strokeWidth="1.4"
              strokeLinejoin="round"
            />
          </svg>
          {isSaved(detail.slug) ? t.bookmarkRemove : t.bookmarkAdd}
        </button>
      </header>

      {/*
        A record whose class mirrors one of the source workbook's sheets is
        laid out as that sheet: its columns, in its order, under its own
        headings, and nothing else. Showing the full registry instead made
        columns a sheet does not have (QTc on an ADHD drug) read as missing
        data rather than not-applicable. Classes with no profile — older
        records from the website snapshot — keep the grouped view.
      */}
      {profile ? (
        <section className="field-group" aria-labelledby="record-fields">
          <h2 id="record-fields" className="sr-only">
            {t.recordFields}
          </h2>
          <dl className="field-list">
            {profile.map((entry) => {
              const key = resolveProfileField(
                entry,
                (k) => detail.fields[k]?.value.state === 'provided',
              );
              return (
                <div className="field-row" key={entry.key}>
                  <dt>{profileLabel(entry, locale, key)}</dt>
                  <dd>
                    {/*
                      Sources are shown while editing, not while reading. Every
                      field of a workbook record cites the same workbook, so on
                      the reading page it repeated one line under every entry
                      and buried the clinical text. The citations are untouched
                      — they still gate publication and still appear in the
                      editorial view.
                    */}
                    <FieldValueView
                      fieldKey={key}
                      field={detail.fields[key]}
                      citations={wantsDraft ? detail.citations : []}
                    />
                  </dd>
                </div>
              );
            })}
          </dl>
        </section>
      ) : (
        FIELD_GROUPS.map((group) => {
          const fields = fieldsInGroup(group);
          if (fields.length === 0) return null;

          return (
            <section className="field-group" key={group} aria-labelledby={`group-${group}`}>
              <h2 id={`group-${group}`}>{FIELD_GROUP_LABELS[group][locale]}</h2>
              <dl className="field-list">
                {fields.map((field) => (
                  <div className="field-row" key={field.key}>
                    <dt>{field.label[locale]}</dt>
                    <dd>
                      <FieldValueView
                        fieldKey={field.key}
                        field={detail.fields[field.key]}
                        citations={detail.citations}
                      />
                    </dd>
                  </div>
                ))}
              </dl>
            </section>
          );
        })
      )}

      {wantsDraft && (
      <section className="field-group" aria-labelledby="group-provenance">
        <h2 id="group-provenance">{t.sources}</h2>
        {detail.source.label || detail.source.document ? (
          <dl className="field-list">
            <div className="field-row">
              <dt>{t.sourceLabel}</dt>
              <dd>{detail.source.label ?? '—'}</dd>
            </div>
            {detail.source.document && (
              <div className="field-row">
                <dt>Document</dt>
                <dd className="mono">{detail.source.document}</dd>
              </div>
            )}
          </dl>
        ) : (
          <p className="value-state">{t.noSources}</p>
        )}
      </section>
      )}

      {/*
        Provenance and the disclaimer sit below the content rather than above
        it. The banner across every screen already carries the warning, and
        stacking a second one over each record buys nothing: warnings that
        always appear stop being read, which is the opposite of the point.
      */}
      <footer className="record-footer">
        <dl className="record-meta">
          <div>
            <dt>{t.versionLabel}</dt>
            <dd>{detail.versionNumber}</dd>
          </div>
          {detail.reviewedAt && (
            <div>
              <dt>{t.reviewDate}</dt>
              <dd>{detail.reviewedAt}</dd>
            </div>
          )}
          {detail.publishedAt && (
            <div>
              <dt>{t.lastPublished}</dt>
              <dd>{detail.publishedAt.slice(0, 10)}</dd>
            </div>
          )}
          <div>
            <dt>{t.validationStatus}</dt>
            <dd>{detail.validationStatus}</dd>
          </div>
        </dl>

        {unvalidated && (
          <p className="record-disclaimer">
            <strong>{t.disclaimerHeading}:</strong> {t.unvalidatedRecordDetail}
          </p>
        )}

        {/*
          Which checks are outstanding is editorial detail — missing citations,
          no review date. It is what an editor needs in order to act, and noise
          to a clinician reading the record, so it is shown only to the roles
          that can do something about it.
        */}
        {can('catalogue:read_unpublished') && detail.overriddenBlockers.length > 0 && (
          <details className="record-internal">
            <summary>{t.outstandingChecks}</summary>
            <ul>
              {detail.overriddenBlockers.map((blocker, i) => (
                <li key={i}>{blocker.message}</li>
              ))}
            </ul>
          </details>
        )}

        {can('catalogue:read_unpublished') && (
          <p style={{ marginBlockStart: 16 }}>
            <Link to={`/review?slug=${encodeURIComponent(detail.slug)}`} className="btn btn-sm btn-secondary">
              {t.navReview}
            </Link>
          </p>
        )}
      </footer>

      {/* Editorial tooling, below everything a reader needs. */}
      {can('catalogue:edit_draft') && (
        <section style={{ marginBlockStart: 32 }}>
          <SourceSuggestions
            slug={detail.slug}
            versionId={detail.versionId}
            onAttached={reload}
          />
        </section>
      )}
    </article>
  );
}
