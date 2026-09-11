-- Derived search index. Rebuilt from medication_versions by the application,
-- which normalises text with the same code the query parser uses, so the two
-- can never drift. Nothing here is authoritative clinical content.
CREATE TABLE search_documents (
  version_id    uuid NOT NULL REFERENCES medication_versions (id) ON DELETE CASCADE,
  medication_id uuid NOT NULL REFERENCES medications (id) ON DELETE CASCADE,
  locale        text NOT NULL CHECK (locale IN ('en', 'he')),
  state         workflow_state NOT NULL,

  -- Normalised text, split by weight class.
  name_text     text NOT NULL DEFAULT '',  -- generic name + trade names + aliases
  class_text    text NOT NULL DEFAULT '',  -- therapeutic group, class, family, mechanism
  body_text     text NOT NULL DEFAULT '',  -- indications, side effects, notes, keywords

  -- Original-cased values used to render the compact result row without a
  -- second query.
  display       jsonb NOT NULL DEFAULT '{}'::jsonb,

  tsv tsvector GENERATED ALWAYS AS (
      setweight(to_tsvector('simple', coalesce(name_text, '')),  'A')
   || setweight(to_tsvector('simple', coalesce(class_text, '')), 'B')
   || setweight(to_tsvector('simple', coalesce(body_text, '')),  'C')
  ) STORED,

  PRIMARY KEY (version_id, locale)
);
CREATE INDEX search_documents_tsv_idx   ON search_documents USING gin (tsv);
CREATE INDEX search_documents_name_trgm ON search_documents USING gin (name_text gin_trgm_ops);
CREATE INDEX search_documents_body_trgm ON search_documents USING gin (body_text gin_trgm_ops);
CREATE INDEX search_documents_state_idx ON search_documents (state);
CREATE INDEX search_documents_med_idx   ON search_documents (medication_id);

-- Facet values for the filter UI, also derived.
CREATE TABLE search_facets (
  version_id    uuid NOT NULL REFERENCES medication_versions (id) ON DELETE CASCADE,
  medication_id uuid NOT NULL REFERENCES medications (id) ON DELETE CASCADE,
  state         workflow_state NOT NULL,
  facet         text NOT NULL,   -- therapeutic_group | drug_class | drug_family | formulation
  value         text NOT NULL,   -- original-cased value, exactly as stored
  value_normalized text NOT NULL,
  PRIMARY KEY (version_id, facet, value_normalized)
);
CREATE INDEX search_facets_lookup_idx ON search_facets (facet, value_normalized, state);
CREATE INDEX search_facets_med_idx    ON search_facets (medication_id);
