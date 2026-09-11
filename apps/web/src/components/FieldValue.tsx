import { VALUE_STATE_LABELS, getField } from '@med/shared';
import { useI18n } from '../i18n.ts';
import type { CitationDto, ResolvedFieldDto } from '../lib/types.ts';

/**
 * Renders one clinical field.
 *
 * A field with no content shows its explicit state — Unknown, Not supplied or
 * Not applicable — rather than an empty cell or a dash, so a reader can tell
 * "nobody has recorded this" apart from "this does not apply".
 */
export function FieldValueView({
  fieldKey,
  field,
  citations = [],
}: {
  fieldKey: string;
  field: ResolvedFieldDto | undefined;
  citations?: CitationDto[];
}) {
  const { locale, t } = useI18n();
  const definition = getField(fieldKey);

  if (!field || field.value.state !== 'provided' || !field.value.text) {
    const state = field?.value.state ?? 'not_supplied';
    return (
      <span className="value-state">
        {VALUE_STATE_LABELS[state][locale]}
      </span>
    );
  }

  const text = field.value.text;
  const fieldCitations = citations.filter((c) => c.fieldKey === fieldKey);

  return (
    <>
      {definition?.list ? (
        <ul className="chip-list">
          {splitList(text).map((item, i) => (
            <li key={i}>{item}</li>
          ))}
        </ul>
      ) : (
        <span style={{ whiteSpace: 'pre-wrap' }}>{text}</span>
      )}

      {field.fallback && (
        <span className="badge" style={{ marginInlineStart: 8 }}>
          {field.locale === 'en' ? t.showingOtherLanguage : t.showingOtherLanguageHe}
        </span>
      )}

      {fieldCitations.length > 0 && (
        <ul className="citation-list">
          {fieldCitations.map((citation) => (
            <li key={citation.id} className="citation">
              <strong>{citation.title}</strong>
              <div className="citation-meta">
                {citation.page && <span>{t.page} {citation.page}</span>}
                {citation.jurisdiction && <span>{t.jurisdiction}: {citation.jurisdiction}</span>}
                <span className={`badge ${citation.approvalStatus === 'approved' ? 'badge-success' : ''}`}>
                  {approvalLabel(citation.approvalStatus, locale)}
                </span>
                {citation.reviewedAt && <span>{t.reviewDate}: {citation.reviewedAt}</span>}
              </div>
              {citation.url && (
                <a href={citation.url} target="_blank" rel="noreferrer noopener">
                  {citation.url}
                </a>
              )}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function approvalLabel(status: CitationDto['approvalStatus'], locale: 'en' | 'he'): string {
  const labels = {
    approved: { en: 'Approved', he: 'מאושר' },
    off_label: { en: 'Off-label', he: 'Off-label' },
    unknown: { en: 'Unknown', he: 'לא ידוע' },
    not_applicable: { en: 'Not applicable', he: 'לא רלוונטי' },
  };
  return labels[status][locale];
}

/**
 * Splits a comma, semicolon or line separated value for chip display.
 * Purely presentational: the stored text is untouched, and anything that does
 * not look like a list is left as one item.
 *
 * Lines are a separator in their own right because the graded side-effect
 * matrix arrives one scored effect per line ("WG: ++"), and those commonly
 * contain commas of their own.
 */
function splitList(text: string): string[] {
  if (text.length > 400) return [text];
  const lines = text.split('\n').map((p) => p.trim()).filter(Boolean);
  if (lines.length > 1) return lines;
  const parts = text.split(/\s*[,;]\s*/).map((p) => p.trim()).filter(Boolean);
  return parts.length > 1 ? parts : [text];
}
