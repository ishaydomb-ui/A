import { useCallback, useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useI18n } from '../i18n.ts';
import { useAuth } from '../lib/auth.tsx';
import { ApiError, api, qs } from '../lib/api.ts';
import type { ReviewFinding } from '../lib/types.ts';
import { Notice } from '../components/Notice.tsx';
import { Spinner } from '../components/Spinner.tsx';

interface QueueItem {
  version_id: string;
  version_number: number;
  state: string;
  validation_status: string;
  slug: string;
  name_en: string | null;
  name_he: string | null;
  blocking_findings: number;
  submitted_at: string | null;
}

/**
 * The review report.
 *
 * Everything the import detected but refused to change on its own ends up
 * here, alongside the queue of records waiting for a clinical decision.
 */
export function ReviewPage() {
  const { t } = useI18n();
  const { can } = useAuth();
  const [params, setParams] = useSearchParams();

  const [findings, setFindings] = useState<ReviewFinding[]>([]);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const status = params.get('status') ?? 'open';
  const severity = params.get('severity') ?? '';
  const slug = params.get('slug') ?? '';

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    Promise.all([
      api.get<{ findings: ReviewFinding[] }>(
        `/api/review/findings${qs({
          status: status || undefined,
          severity: severity || undefined,
          medicationSlug: slug || undefined,
        })}`,
      ),
      can('catalogue:read_unpublished')
        ? api.get<{ queue: QueueItem[] }>('/api/review/queue')
        : Promise.resolve({ queue: [] as QueueItem[] }),
    ])
      .then(([f, q]) => {
        setFindings(f.findings);
        setQueue(q.queue);
      })
      .catch((err: unknown) => setError(err instanceof ApiError ? err.message : t.errorGeneric))
      .finally(() => setLoading(false));
  }, [status, severity, slug, can, t]);

  useEffect(load, [load]);

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next);
  }

  return (
    <>
      <h1>{t.navReview}</h1>

      {error && <Notice tone="error">{error}</Notice>}

      {queue.length > 0 && (
        <section aria-labelledby="queue-heading" style={{ marginBlockEnd: 32 }}>
          <h2 id="queue-heading">Awaiting a decision</h2>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">Medication</th>
                  <th scope="col">State</th>
                  <th scope="col">Validation</th>
                  <th scope="col">Blocking findings</th>
                </tr>
              </thead>
              <tbody>
                {queue.map((item) => (
                  <tr key={item.version_id}>
                    <th scope="row" style={{ fontWeight: 600 }}>
                      <Link to={`/medications/${encodeURIComponent(item.slug)}?draft=1`}>
                        {item.name_en ?? item.name_he ?? item.slug}
                      </Link>
                    </th>
                    <td>
                      <span className="badge">{item.state.replace(/_/g, ' ')}</span>
                    </td>
                    <td className="small muted">{item.validation_status}</td>
                    <td>
                      {item.blocking_findings > 0 ? (
                        <span className="badge badge-high">{item.blocking_findings}</span>
                      ) : (
                        <span className="badge badge-success">0</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section aria-labelledby="findings-heading">
        <div className="row" style={{ alignItems: 'flex-start' }}>
          <div style={{ flex: 1, minWidth: 260 }}>
            <h2 id="findings-heading">Data quality findings</h2>
            <p className="muted small">
              Detected during import. Nothing listed here has been corrected automatically — each
              item needs a human decision.
            </p>
          </div>
          {/* A plain link, so the browser downloads it and the session cookie
              is sent exactly as it is for any other request. */}
          <a className="btn btn-secondary" href="/api/review/findings/export.xlsx" download>
            Download all findings (.xlsx)
          </a>
        </div>

        <div className="row" style={{ marginBlockEnd: 16 }}>
          <div>
            <label htmlFor="filter-status" className="small">
              Status
            </label>
            <select
              id="filter-status"
              value={status}
              onChange={(e) => setFilter('status', e.target.value)}
            >
              <option value="">All</option>
              <option value="open">Open</option>
              <option value="acknowledged">Acknowledged</option>
              <option value="resolved">Resolved</option>
              <option value="wont_fix">Won't fix</option>
            </select>
          </div>
          <div>
            <label htmlFor="filter-severity" className="small">
              Severity
            </label>
            <select
              id="filter-severity"
              value={severity}
              onChange={(e) => setFilter('severity', e.target.value)}
            >
              <option value="">All</option>
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>
          </div>
        </div>

        {loading ? (
          <Spinner />
        ) : findings.length === 0 ? (
          <div className="empty-state">
            <p>No findings match these filters.</p>
          </div>
        ) : (
          <ul className="stack" style={{ listStyle: 'none', padding: 0 }}>
            {findings.map((finding) => (
              <FindingCard key={finding.id} finding={finding} onChanged={load} />
            ))}
          </ul>
        )}
      </section>
    </>
  );
}

function FindingCard({ finding, onChanged }: { finding: ReviewFinding; onChanged(): void }) {
  const { t } = useI18n();
  const { can } = useAuth();
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);

  const closed = finding.status === 'resolved' || finding.status === 'wont_fix';

  async function update(status: string) {
    setBusy(true);
    setError(null);
    try {
      await api.patch(`/api/review/findings/${finding.id}`, {
        status,
        resolutionNote: note || undefined,
      });
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t.errorGeneric);
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="card">
      <div className="row" style={{ marginBlockEnd: 8 }}>
        <span className={`badge badge-${finding.severity}`}>{finding.severity}</span>
        <strong>{finding.issue_type}</strong>
        <span className="muted small">· {finding.scope}</span>
        {finding.field_key && <span className="badge">{finding.field_key}</span>}
        <span className="spacer" />
        <span className={`badge ${closed ? 'badge-success' : ''}`}>
          {finding.status.replace(/_/g, ' ')}
        </span>
      </div>

      <p style={{ marginBlockEnd: 8 }}>{finding.evidence}</p>
      <p className="small muted">
        <strong>Recommended:</strong> {finding.recommended_action}
      </p>

      {finding.medication_slug && (
        <p className="small">
          <Link to={`/medications/${encodeURIComponent(finding.medication_slug)}?draft=1`}>
            {finding.medication_slug}
          </Link>
        </p>
      )}

      {finding.resolution_note && (
        <Notice tone="success">
          <strong>{finding.resolved_by_email}:</strong> {finding.resolution_note}
        </Notice>
      )}

      {can('review:resolve') && !closed && (
        <>
          <button
            type="button"
            className="btn btn-sm btn-secondary"
            aria-expanded={expanded}
            onClick={() => setExpanded((v) => !v)}
          >
            Record a decision
          </button>

          {expanded && (
            <div style={{ marginBlockStart: 12 }}>
              {error && <Notice tone="error">{error}</Notice>}
              <div className="field">
                <label htmlFor={`note-${finding.id}`}>
                  How was this resolved? <span className="muted">({t.required})</span>
                </label>
                <textarea
                  id={`note-${finding.id}`}
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  required
                />
                <p className="hint">
                  Closing a finding is a clinical decision and is recorded against your account.
                </p>
              </div>
              <div className="row">
                <button
                  type="button"
                  className="btn btn-sm btn-primary"
                  disabled={busy || !note.trim()}
                  onClick={() => void update('resolved')}
                >
                  Mark resolved
                </button>
                <button
                  type="button"
                  className="btn btn-sm btn-secondary"
                  disabled={busy || !note.trim()}
                  onClick={() => void update('wont_fix')}
                >
                  Won't fix
                </button>
                <button
                  type="button"
                  className="btn btn-sm btn-secondary"
                  disabled={busy}
                  onClick={() => void update('acknowledged')}
                >
                  Acknowledge
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </li>
  );
}
