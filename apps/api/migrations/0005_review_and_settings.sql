-- Review findings: the mechanism that keeps the system from silently fixing
-- clinical data. Anything suspicious is recorded here for a human decision.
CREATE TYPE finding_severity AS ENUM ('high', 'medium', 'low', 'info');
CREATE TYPE finding_status   AS ENUM ('open', 'acknowledged', 'resolved', 'wont_fix');

CREATE TABLE review_findings (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  batch_id      uuid REFERENCES import_batches (id) ON DELETE CASCADE,
  medication_id uuid REFERENCES medications (id) ON DELETE CASCADE,
  version_id    uuid REFERENCES medication_versions (id) ON DELETE CASCADE,
  row_number    integer,
  severity      finding_severity NOT NULL,
  scope         text NOT NULL,
  field_key     text,
  issue_type    text NOT NULL,
  evidence      text NOT NULL,
  recommended_action text NOT NULL,
  status        finding_status NOT NULL DEFAULT 'open',
  -- Set when a finding is auto-detected rather than entered by a person.
  detector      text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  created_by    uuid REFERENCES users (id) ON DELETE SET NULL,
  resolved_at   timestamptz,
  resolved_by   uuid REFERENCES users (id) ON DELETE SET NULL,
  resolution_note text
);
CREATE INDEX review_findings_batch_idx  ON review_findings (batch_id, severity);
CREATE INDEX review_findings_med_idx    ON review_findings (medication_id, status);
CREATE INDEX review_findings_status_idx ON review_findings (status, severity);

-- Blocking findings must be closed before the affected record can publish.
CREATE OR REPLACE FUNCTION has_blocking_findings(p_medication_id uuid)
RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT EXISTS (
    SELECT 1 FROM review_findings
    WHERE medication_id = p_medication_id
      AND severity = 'high'
      AND status IN ('open', 'acknowledged')
  );
$$;

-- System settings. Values that need the owner's sign-off (institution name,
-- legal texts, branding) live here rather than being hard-coded.
CREATE TABLE settings (
  key         text PRIMARY KEY,
  value       jsonb NOT NULL,
  -- Marks a value that is a placeholder awaiting the owner's approval.
  needs_approval boolean NOT NULL DEFAULT false,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  updated_by  uuid REFERENCES users (id) ON DELETE SET NULL
);

INSERT INTO settings (key, value, needs_approval) VALUES
  ('institution_name', '"(pending approval)"'::jsonb, true),
  ('contact_email',    '"(pending approval)"'::jsonb, true),
  ('legal.privacy_policy', '"(placeholder — awaiting owner approval)"'::jsonb, true),
  ('legal.terms',          '"(placeholder — awaiting owner approval)"'::jsonb, true);
