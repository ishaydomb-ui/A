/**
 * Provenance model. Any clinical claim may carry one or more citations; the
 * workbook's audit found the source website had none, and publication is
 * blocked until required citations exist.
 */
export const APPROVAL_STATUSES = ['approved', 'off_label', 'unknown', 'not_applicable'] as const;
export type ApprovalStatus = (typeof APPROVAL_STATUSES)[number];

export const APPROVAL_STATUS_LABELS: Record<ApprovalStatus, { en: string; he: string }> = {
  approved: { en: 'Approved', he: 'מאושר' },
  off_label: { en: 'Off-label', he: 'Off-label' },
  unknown: { en: 'Unknown', he: 'לא ידוע' },
  not_applicable: { en: 'Not applicable', he: 'לא רלוונטי' },
};

export interface Citation {
  id?: string;
  /** Which field of the medication this citation supports. */
  fieldKey: string;
  title: string;
  documentRef: string | null;
  url: string | null;
  page: string | null;
  /** Regulatory jurisdiction the claim applies to, e.g. "IL", "US", "EU". */
  jurisdiction: string | null;
  approvalStatus: ApprovalStatus;
  reviewedAt: string | null;
  reviewedBy?: string | null;
}

/**
 * Fields that must carry at least one citation before a record may be
 * published. Derived directly from the workbook's "Claims lack citations"
 * and "Approval status not modeled" findings.
 */
export const CITATION_REQUIRED_FIELDS: readonly string[] = [
  'adult_indications',
  'pediatric_indications',
  'qtc_adults',
  'qtc_pediatrics',
  'contraindications',
  'maximum_dose',
];
