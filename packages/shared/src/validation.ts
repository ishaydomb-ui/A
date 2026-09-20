/**
 * Severity levels for import and review findings, mirroring the workbook's
 * own Data Quality Review sheet.
 */
export const SEVERITIES = ['high', 'medium', 'low', 'info'] as const;
export type Severity = (typeof SEVERITIES)[number];

export const FINDING_STATUSES = ['open', 'acknowledged', 'resolved', 'wont_fix'] as const;
export type FindingStatus = (typeof FINDING_STATUSES)[number];

export interface ReviewFinding {
  id?: string;
  severity: Severity;
  /** Medication slug or a scope label such as "multiple". */
  scope: string;
  fieldKey: string | null;
  issueType: string;
  evidence: string;
  recommendedAction: string;
  status: FindingStatus;
}

/** Language of a value, for bilingual content. */
export const LOCALES = ['en', 'he'] as const;
export type Locale = (typeof LOCALES)[number];

export function isLocale(v: unknown): v is Locale {
  return v === 'en' || v === 'he';
}

export const DIRECTION: Record<Locale, 'ltr' | 'rtl'> = { en: 'ltr', he: 'rtl' };
