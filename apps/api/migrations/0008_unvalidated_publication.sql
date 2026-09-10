-- Preview publication.
--
-- The owner may need the catalogue visible for testing before any clinical
-- review has happened. That is a legitimate need, but it must never be a
-- quiet bypass: the allowance is an explicit system setting, each affected
-- version is flagged, the blockers that were overridden are recorded on the
-- row itself, and the interface says so wherever the record appears.
ALTER TABLE medication_versions
  ADD COLUMN published_unvalidated boolean NOT NULL DEFAULT false,
  -- The publication blockers that were in force when it was published anyway.
  ADD COLUMN overridden_blockers jsonb NOT NULL DEFAULT '[]'::jsonb;

CREATE INDEX medication_versions_unvalidated_idx
  ON medication_versions (published_unvalidated) WHERE published_unvalidated;

INSERT INTO settings (key, value, needs_approval) VALUES
  ('publication.allow_unvalidated', 'false'::jsonb, false),
  -- Shown across the whole interface while the allowance is on.
  ('publication.unvalidated_notice',
   '"This catalogue is in evaluation. Its content comes from an unverified website snapshot, has not been checked against an authoritative source, and has not been reviewed by a clinician. Do not use it for clinical decisions."'::jsonb,
   false)
ON CONFLICT (key) DO NOTHING;
