-- The application connects as a least-privilege role that can never rewrite
-- history: the audit log and the revision trail are insert-only.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = current_user) THEN
    EXECUTE format('REVOKE UPDATE, DELETE, TRUNCATE ON TABLE audit_log FROM %I', current_user);
    EXECUTE format('REVOKE UPDATE, DELETE, TRUNCATE ON TABLE revisions FROM %I', current_user);
  END IF;
EXCEPTION WHEN OTHERS THEN
  -- Owners cannot revoke from themselves; the deployment guide creates a
  -- separate app role for production where this takes effect.
  RAISE NOTICE 'Skipping insert-only grants: %', SQLERRM;
END $$;

-- Defence in depth: block UPDATE/DELETE with triggers, which apply to table
-- owners too.
CREATE OR REPLACE FUNCTION deny_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is append-only; % is not permitted', TG_TABLE_NAME, TG_OP
    USING ERRCODE = 'insufficient_privilege';
END $$;

CREATE TRIGGER audit_log_append_only
  BEFORE UPDATE OR DELETE ON audit_log
  FOR EACH ROW EXECUTE FUNCTION deny_mutation();

CREATE TRIGGER revisions_append_only
  BEFORE UPDATE OR DELETE ON revisions
  FOR EACH ROW EXECUTE FUNCTION deny_mutation();
