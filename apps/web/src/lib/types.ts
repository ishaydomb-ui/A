import type { Capability, Locale, Role } from '@med/shared';

export interface PublicUser {
  id: string;
  email: string;
  displayName: string;
  role: Role;
  status: 'invited' | 'active' | 'suspended' | 'deactivated';
  mfaEnabled: boolean;
  emailVerified: boolean;
  lastLoginAt: string | null;
  createdAt: string;
}

export interface Session {
  user: PublicUser;
  capabilities: Capability[];
}

export interface HighlightSegment {
  text: string;
  match: boolean;
}

export interface SearchHit {
  slug: string;
  medicationId: string;
  versionId: string;
  state: string;
  score: number;
  matchKind: 'exact' | 'prefix' | 'text' | 'fuzzy';
  genericName: string | null;
  tradeNames: string | null;
  therapeuticGroup: string | null;
  drugClass: string | null;
  displayLocale: Locale;
  fallback: boolean;
  highlights: { genericName: HighlightSegment[]; tradeNames: HighlightSegment[] };
}

export interface SearchResponse {
  hits: SearchHit[];
  total: number;
  didYouMean: string | null;
  usedFuzzy: boolean;
  limit: number;
  offset: number;
  maxCompare: number;
}

export interface FieldValueDto {
  state: 'provided' | 'unknown' | 'not_supplied' | 'not_applicable';
  text: string | null;
}

export interface ResolvedFieldDto {
  value: FieldValueDto;
  locale: Locale;
  fallback: boolean;
}

export interface CitationDto {
  id: string;
  fieldKey: string;
  title: string;
  documentRef: string | null;
  url: string | null;
  page: string | null;
  jurisdiction: string | null;
  approvalStatus: 'approved' | 'off_label' | 'unknown' | 'not_applicable';
  reviewedAt: string | null;
}

export interface MedicationDetail {
  slug: string;
  versionId: string;
  versionNumber: number;
  state: string;
  validationStatus: string;
  reviewedAt: string | null;
  source: { label: string | null; document: string | null; version: string | null };
  publishedAt: string | null;
  fields: Record<string, ResolvedFieldDto>;
  citations: CitationDto[];
}

export interface FacetValue { value: string; count: number }
export type Facets = Record<string, FacetValue[]>;

export interface ReviewFinding {
  id: string;
  severity: 'high' | 'medium' | 'low' | 'info';
  scope: string;
  field_key: string | null;
  issue_type: string;
  evidence: string;
  recommended_action: string;
  status: 'open' | 'acknowledged' | 'resolved' | 'wont_fix';
  detector: string | null;
  row_number: number | null;
  batch_id: string | null;
  medication_slug: string | null;
  created_at: string;
  resolved_at: string | null;
  resolution_note: string | null;
  resolved_by_email: string | null;
}

export interface PublicSettings {
  settings: Record<string, { value: unknown; needsApproval: boolean }>;
  environment: string;
}
