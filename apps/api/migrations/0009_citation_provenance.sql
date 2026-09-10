-- Where a citation came from.
--
-- A citation attached by hand and one accepted from an external lookup carry
-- different weight, and a reviewer should be able to tell them apart later.
-- Suggestions are never attached automatically, so this records which lookup
-- a person accepted, not that anything was decided for them.
ALTER TABLE citations
  ADD COLUMN source_provider text,
  -- The external identifier, so the same claim can be re-checked later.
  ADD COLUMN external_id text;

CREATE INDEX citations_provider_idx ON citations (source_provider) WHERE source_provider IS NOT NULL;
