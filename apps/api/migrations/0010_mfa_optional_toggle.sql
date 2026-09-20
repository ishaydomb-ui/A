-- Makes mandatory admin MFA a switchable policy instead of a fixed rule, so
-- it can be turned off for a while and back on later without a code change.
-- Defaults to on, matching the behaviour before this migration.
INSERT INTO settings (key, value, needs_approval) VALUES
  ('security.mfa_required_for_admin', 'true'::jsonb, false)
ON CONFLICT (key) DO NOTHING;
