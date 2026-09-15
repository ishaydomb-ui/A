-- Excel import. Uploading a workbook NEVER changes the live catalogue.
-- Rows land in staging, are validated and diffed, reviewed, and only then
-- committed into draft versions that still have to pass clinical review.
CREATE TYPE import_status AS ENUM (
  'uploaded', 'mapped', 'validated', 'committed', 'discarded', 'failed'
);
CREATE TYPE import_row_action AS ENUM (
  'create', 'update', 'unchanged', 'duplicate', 'skip', 'error'
);

CREATE TABLE import_batches (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  original_filename text NOT NULL,
  sheet_name     text,
  -- Original workbook retained verbatim for audit; path is relative to the
  -- configured import storage root.
  storage_path   text NOT NULL,
  sha256         text NOT NULL,
  byte_size      bigint NOT NULL,
  status         import_status NOT NULL DEFAULT 'uploaded',
  -- { excelColumnName: fieldKey | null }
  column_mapping jsonb NOT NULL DEFAULT '{}'::jsonb,
  locale         text NOT NULL DEFAULT 'en' CHECK (locale IN ('en', 'he')),
  source_label   text,
  -- Counts and validation summary produced by the preview step.
  stats          jsonb NOT NULL DEFAULT '{}'::jsonb,
  error_message  text,
  uploaded_at    timestamptz NOT NULL DEFAULT now(),
  uploaded_by    uuid REFERENCES users (id) ON DELETE SET NULL,
  validated_at   timestamptz,
  committed_at   timestamptz,
  committed_by   uuid REFERENCES users (id) ON DELETE SET NULL,
  discarded_at   timestamptz
);
CREATE INDEX import_batches_status_idx ON import_batches (status, uploaded_at DESC);
CREATE UNIQUE INDEX import_batches_sha_idx ON import_batches (sha256) WHERE status <> 'discarded';

ALTER TABLE medication_versions
  ADD CONSTRAINT medication_versions_import_batch_fkey
  FOREIGN KEY (import_batch_id) REFERENCES import_batches (id) ON DELETE SET NULL;

CREATE TABLE import_rows (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  batch_id      uuid NOT NULL REFERENCES import_batches (id) ON DELETE CASCADE,
  row_number    integer NOT NULL,   -- 1-based row number in the source sheet
  -- Raw cells exactly as read from the workbook.
  raw           jsonb NOT NULL DEFAULT '{}'::jsonb,
  -- Cells mapped onto field keys, still verbatim.
  mapped        jsonb NOT NULL DEFAULT '{}'::jsonb,
  slug          text,
  action        import_row_action NOT NULL DEFAULT 'create',
  matched_medication_id uuid REFERENCES medications (id) ON DELETE SET NULL,
  -- [{ fieldKey, locale, before, after }] versus the current version.
  diff          jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_version_id uuid REFERENCES medication_versions (id) ON DELETE SET NULL
);
CREATE UNIQUE INDEX import_rows_batch_row_key ON import_rows (batch_id, row_number);
CREATE INDEX import_rows_action_idx ON import_rows (batch_id, action);
CREATE INDEX import_rows_slug_idx   ON import_rows (batch_id, slug);
