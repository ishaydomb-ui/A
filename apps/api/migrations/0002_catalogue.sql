CREATE TYPE workflow_state AS ENUM (
  'draft', 'in_clinical_review', 'changes_requested', 'approved', 'published', 'archived'
);
CREATE TYPE approval_status AS ENUM ('approved', 'off_label', 'unknown', 'not_applicable');

-- A medication is a stable identity; its content lives in immutable versions.
CREATE TABLE medications (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug        text        NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  created_by  uuid REFERENCES users (id) ON DELETE SET NULL,
  archived_at timestamptz
);
CREATE UNIQUE INDEX medications_slug_key ON medications (slug);

-- Every edit creates a new row. Rows are never updated in place except for
-- the workflow columns, so the content history is immutable and auditable.
CREATE TABLE medication_versions (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  medication_id  uuid NOT NULL REFERENCES medications (id) ON DELETE CASCADE,
  version_number integer NOT NULL,
  state          workflow_state NOT NULL DEFAULT 'draft',

  -- { field_key: { en: {state,text}, he: {state,text} } }
  -- Verbatim source content. Never silently corrected or normalised.
  data           jsonb NOT NULL DEFAULT '{}'::jsonb,

  -- Provenance of the version as a whole.
  source_label     text,             -- e.g. "Central export 2026-09 (official)"
  source_document  text,
  source_version   text,
  reviewed_at      date,             -- clinical review date for this content
  validation_status text NOT NULL DEFAULT 'Unvalidated website snapshot',

  import_batch_id uuid,              -- FK added in 0004

  created_at   timestamptz NOT NULL DEFAULT now(),
  created_by   uuid REFERENCES users (id) ON DELETE SET NULL,
  submitted_at timestamptz,
  submitted_by uuid REFERENCES users (id) ON DELETE SET NULL,
  reviewed_by  uuid REFERENCES users (id) ON DELETE SET NULL,
  review_decided_at timestamptz,
  approved_at  timestamptz,
  approved_by  uuid REFERENCES users (id) ON DELETE SET NULL,
  published_at timestamptz,
  published_by uuid REFERENCES users (id) ON DELETE SET NULL,
  change_reason text
);
CREATE UNIQUE INDEX medication_versions_number_key
  ON medication_versions (medication_id, version_number);
CREATE INDEX medication_versions_state_idx ON medication_versions (state);
CREATE INDEX medication_versions_medication_idx
  ON medication_versions (medication_id, version_number DESC);

-- At most one published version per medication at any time.
CREATE UNIQUE INDEX medication_versions_one_published
  ON medication_versions (medication_id) WHERE state = 'published';
-- At most one open editorial version (draft or in-flight) per medication.
CREATE UNIQUE INDEX medication_versions_one_open
  ON medication_versions (medication_id)
  WHERE state IN ('draft', 'in_clinical_review', 'changes_requested', 'approved');

-- Aliases power search: trade names, abbreviations, Hebrew names, and known
-- misspellings. Marked by kind so provenance stays clear.
CREATE TYPE alias_kind AS ENUM ('trade_name', 'abbreviation', 'hebrew_name', 'synonym', 'misspelling');
CREATE TABLE medication_aliases (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  medication_id  uuid NOT NULL REFERENCES medications (id) ON DELETE CASCADE,
  alias          text NOT NULL,
  alias_normalized text NOT NULL,
  kind           alias_kind NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  created_by     uuid REFERENCES users (id) ON DELETE SET NULL
);
CREATE INDEX medication_aliases_medication_idx ON medication_aliases (medication_id);
CREATE INDEX medication_aliases_norm_trgm
  ON medication_aliases USING gin (alias_normalized gin_trgm_ops);
CREATE UNIQUE INDEX medication_aliases_unique
  ON medication_aliases (medication_id, alias_normalized, kind);

-- Citations attach a source to an individual clinical claim.
CREATE TABLE citations (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  version_id    uuid NOT NULL REFERENCES medication_versions (id) ON DELETE CASCADE,
  field_key     text NOT NULL,
  title         text NOT NULL,
  document_ref  text,
  url           text,
  page          text,
  jurisdiction  text,
  approval_status approval_status NOT NULL DEFAULT 'unknown',
  reviewed_at   date,
  reviewed_by   uuid REFERENCES users (id) ON DELETE SET NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  created_by    uuid REFERENCES users (id) ON DELETE SET NULL
);
CREATE INDEX citations_version_idx ON citations (version_id);
CREATE INDEX citations_field_idx ON citations (version_id, field_key);

-- Immutable revision trail: who did what, when, and exactly what changed.
CREATE TABLE revisions (
  id            bigserial PRIMARY KEY,
  medication_id uuid NOT NULL REFERENCES medications (id) ON DELETE CASCADE,
  version_id    uuid REFERENCES medication_versions (id) ON DELETE SET NULL,
  occurred_at   timestamptz NOT NULL DEFAULT now(),
  actor_id      uuid REFERENCES users (id) ON DELETE SET NULL,
  actor_email   text,
  action        text NOT NULL,        -- created | edited | transitioned | imported
  from_state    workflow_state,
  to_state      workflow_state,
  reason        text,
  -- [{ fieldKey, locale, before: {state,text}, after: {state,text} }]
  field_diff    jsonb NOT NULL DEFAULT '[]'::jsonb
);
CREATE INDEX revisions_medication_idx ON revisions (medication_id, occurred_at DESC);
CREATE INDEX revisions_version_idx ON revisions (version_id);
