import { useCallback, useEffect, useRef, useState } from 'react';
import { FIELDS } from '@med/shared';
import { useI18n } from '../i18n.ts';
import { useAuth } from '../lib/auth.tsx';
import { ApiError, api, request } from '../lib/api.ts';
import { Notice } from '../components/Notice.tsx';
import { Spinner } from '../components/Spinner.tsx';

interface SheetSummary { name: string; rowCount: number; headers: string[] }
interface MappingSuggestion { header: string; fieldKey: string | null; confidence: string; meta?: string }
interface UploadResult {
  batchId: string;
  sheets: SheetSummary[];
  suggestedSheet: string | null;
  suggestedMapping: MappingSuggestion[];
}
interface BatchStats {
  totalRows: number; create: number; update: number; unchanged: number;
  duplicate: number; error: number;
  findings: { high: number; medium: number; low: number; info: number };
}
interface FieldChange { fieldKey: string; locale: string; before: { text: string | null } | null; after: { text: string | null } | null; kind: string }
interface BatchRow {
  rowNumber: number; slug: string | null; name: string; action: string;
  diff: FieldChange[]; error?: string;
}
interface ValidateResult { rows: BatchRow[]; findings: unknown[]; stats: BatchStats }
interface BatchSummary {
  id: string; original_filename: string; status: string; stats: BatchStats | Record<string, never>;
  uploaded_at: string; committed_at: string | null; uploaded_by_email: string | null; sheet_name: string | null;
}

/**
 * Excel import.
 *
 * Deliberately a three-step flow — upload, confirm the mapping, review the
 * preview — because an import must never be a single click that silently
 * rewrites clinical content.
 */
export function ImportsPage() {
  const { t } = useI18n();
  const { can } = useAuth();
  const fileRef = useRef<HTMLInputElement>(null);

  const [batches, setBatches] = useState<BatchSummary[]>([]);
  const [upload, setUpload] = useState<UploadResult | null>(null);
  const [sheetName, setSheetName] = useState('');
  const [mapping, setMapping] = useState<Record<string, string | null>>({});
  const [preview, setPreview] = useState<ValidateResult | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const loadBatches = useCallback(() => {
    api
      .get<{ batches: BatchSummary[] }>('/api/imports')
      .then((res) => setBatches(res.batches))
      .catch(() => undefined);
  }, []);

  useEffect(loadBatches, [loadBatches]);

  async function onUpload(event: React.FormEvent) {
    event.preventDefault();
    const file = fileRef.current?.files?.[0];
    if (!file) return;

    setBusy(true);
    setError(null);
    setMessage(null);
    setPreview(null);
    try {
      const form = new FormData();
      form.append('file', file);
      const result = await request<UploadResult>('/api/imports', { method: 'POST', body: form });
      setUpload(result);
      setSheetName(result.suggestedSheet ?? result.sheets[0]?.name ?? '');
      const next: Record<string, string | null> = {};
      for (const suggestion of result.suggestedMapping) next[suggestion.header] = suggestion.fieldKey;
      setMapping(next);
      loadBatches();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t.errorGeneric);
    } finally {
      setBusy(false);
    }
  }

  async function onValidate() {
    if (!upload) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.post<ValidateResult>(`/api/imports/${upload.batchId}/validate`, {
        sheetName,
        mapping,
        locale: 'en',
      });
      setPreview(result);
      setAcknowledged(false);
      loadBatches();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t.errorGeneric);
    } finally {
      setBusy(false);
    }
  }

  async function onCommit() {
    if (!upload) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.post<{ created: number; updated: number; note: string }>(
        `/api/imports/${upload.batchId}/commit`,
        { acknowledgeFindings: acknowledged },
      );
      setMessage(`Created ${result.created}, updated ${result.updated}. ${result.note}`);
      setUpload(null);
      setPreview(null);
      loadBatches();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t.errorGeneric);
    } finally {
      setBusy(false);
    }
  }

  const headers = upload?.sheets.find((s) => s.name === sheetName)?.headers ?? [];
  const highFindings = preview?.stats.findings.high ?? 0;

  return (
    <>
      <h1>{t.navImports}</h1>

      <Notice tone="info">
        Uploading a workbook does not change the live catalogue. Rows are staged, compared against
        the current records, and committed as drafts that still require clinical review before
        anything is published.
      </Notice>

      {error && <Notice tone="error">{error}</Notice>}
      {message && <Notice tone="success">{message}</Notice>}

      <section className="card" aria-labelledby="upload-heading" style={{ marginBlockEnd: 24 }}>
        <h2 id="upload-heading">1. Upload a workbook</h2>
        <form onSubmit={onUpload}>
          <div className="field">
            <label htmlFor="workbook">Excel workbook (.xlsx)</label>
            <input id="workbook" ref={fileRef} type="file" accept=".xlsx,.xls" required />
          </div>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? t.loading : 'Upload'}
          </button>
        </form>
      </section>

      {upload && (
        <section className="card" aria-labelledby="mapping-heading" style={{ marginBlockEnd: 24 }}>
          <h2 id="mapping-heading">2. Confirm the column mapping</h2>
          <p className="muted small">
            Check every column before continuing. A suggestion is only a suggestion — an incorrect
            mapping would put clinical text in the wrong field.
          </p>

          <div className="field" style={{ maxWidth: 380 }}>
            <label htmlFor="sheet">Sheet</label>
            <select id="sheet" value={sheetName} onChange={(e) => setSheetName(e.target.value)}>
              {upload.sheets.map((sheet) => (
                <option key={sheet.name} value={sheet.name}>
                  {sheet.name} ({sheet.rowCount} rows)
                </option>
              ))}
            </select>
          </div>

          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">Column in workbook</th>
                  <th scope="col">Catalogue field</th>
                </tr>
              </thead>
              <tbody>
                {headers.map((header) => (
                  <tr key={header}>
                    <th scope="row">{header}</th>
                    <td>
                      <label htmlFor={`map-${header}`} className="sr-only">
                        Field for column {header}
                      </label>
                      <select
                        id={`map-${header}`}
                        value={mapping[header] ?? ''}
                        onChange={(e) =>
                          setMapping((m) => ({ ...m, [header]: e.target.value || null }))
                        }
                      >
                        <option value="">— Do not import —</option>
                        {FIELDS.map((field) => (
                          <option key={field.key} value={field.key}>
                            {field.label.en}
                          </option>
                        ))}
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <button type="button" className="btn btn-primary" onClick={() => void onValidate()} disabled={busy}>
            {busy ? t.loading : 'Validate and preview'}
          </button>
        </section>
      )}

      {preview && (
        <section className="card" aria-labelledby="preview-heading" style={{ marginBlockEnd: 24 }}>
          <h2 id="preview-heading">3. Review the change report</h2>

          <ul className="chip-list" style={{ marginBlockEnd: 12 }}>
            <li>{preview.stats.totalRows} rows</li>
            <li>{preview.stats.create} new</li>
            <li>{preview.stats.update} changed</li>
            <li>{preview.stats.unchanged} unchanged</li>
            <li>{preview.stats.duplicate} duplicates</li>
            <li>{preview.stats.error} errors</li>
          </ul>

          <p>
            <span className="badge badge-high">{preview.stats.findings.high} high</span>{' '}
            <span className="badge badge-medium">{preview.stats.findings.medium} medium</span>{' '}
            <span className="badge badge-low">{preview.stats.findings.low} low</span>
          </p>

          <div className="table-scroll" style={{ maxHeight: 420 }}>
            <table>
              <thead>
                <tr>
                  <th scope="col">Row</th>
                  <th scope="col">Medication</th>
                  <th scope="col">Action</th>
                  <th scope="col">Changes</th>
                </tr>
              </thead>
              <tbody>
                {preview.rows.map((row) => (
                  <tr key={row.rowNumber}>
                    <td>{row.rowNumber}</td>
                    <th scope="row">{row.name}</th>
                    <td>
                      <span
                        className={`badge ${row.action === 'error' || row.action === 'duplicate' ? 'badge-high' : ''}`}
                      >
                        {row.action}
                      </span>
                      {row.error && <div className="small muted">{row.error}</div>}
                    </td>
                    <td className="small">
                      {row.action === 'update' ? (
                        <details>
                          <summary>{row.diff.length} field(s)</summary>
                          <ul>
                            {row.diff.map((change, i) => (
                              <li key={i}>
                                <strong>{change.fieldKey}</strong>: {change.before?.text ?? '—'} →{' '}
                                {change.after?.text ?? '—'}
                              </li>
                            ))}
                          </ul>
                        </details>
                      ) : row.action === 'create' ? (
                        <span className="muted">new record</span>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {highFindings > 0 && (
            <Notice tone="warning" title="Unresolved high-severity findings">
              <p>
                This batch raised {highFindings} high-severity finding(s). They are listed under{' '}
                {t.navReview} and will keep blocking publication until a clinical reviewer closes
                them.
              </p>
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={acknowledged}
                  onChange={(e) => setAcknowledged(e.target.checked)}
                />
                <span>I have reviewed these findings and want to stage the import as drafts.</span>
              </label>
            </Notice>
          )}

          {can('import:commit') ? (
            <button
              type="button"
              className="btn btn-primary"
              disabled={busy || (highFindings > 0 && !acknowledged)}
              onClick={() => void onCommit()}
            >
              {busy ? t.loading : 'Stage as drafts'}
            </button>
          ) : (
            <Notice tone="info">
              Your role can prepare an import but not commit it. An administrator must complete this
              step.
            </Notice>
          )}
        </section>
      )}

      <section aria-labelledby="history-heading">
        <h2 id="history-heading">Import history</h2>
        {batches.length === 0 ? (
          <Spinner />
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">File</th>
                  <th scope="col">Sheet</th>
                  <th scope="col">Status</th>
                  <th scope="col">Uploaded</th>
                  <th scope="col">By</th>
                </tr>
              </thead>
              <tbody>
                {batches.map((batch) => (
                  <tr key={batch.id}>
                    <th scope="row" className="mono">
                      {batch.original_filename}
                    </th>
                    <td>{batch.sheet_name ?? '—'}</td>
                    <td>
                      <span className={`badge ${batch.status === 'committed' ? 'badge-success' : ''}`}>
                        {batch.status}
                      </span>
                    </td>
                    <td className="small">{batch.uploaded_at.slice(0, 16).replace('T', ' ')}</td>
                    <td className="small muted">{batch.uploaded_by_email ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
