-- Extensions -----------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS pg_trgm;    -- trigram similarity for typo tolerance
CREATE EXTENSION IF NOT EXISTS unaccent;   -- diacritic folding

-- Identity -------------------------------------------------------------------
CREATE TYPE user_role AS ENUM ('physician', 'clinical_reviewer', 'editor', 'admin');
CREATE TYPE user_status AS ENUM ('invited', 'active', 'suspended', 'deactivated');

CREATE TABLE users (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  -- `email` keeps the address as the admin typed it, for display.
  -- `email_normalized` is the lower-cased form used for every lookup.
  email              text        NOT NULL,
  email_normalized   text        NOT NULL,
  display_name       text        NOT NULL,
  role               user_role   NOT NULL,
  status             user_status NOT NULL DEFAULT 'invited',
  -- Argon2id encoded hash. Never a plaintext or reversible value.
  password_hash      text,
  password_changed_at timestamptz,
  email_verified_at  timestamptz,
  mfa_secret         text,               -- TOTP secret, encrypted at rest
  mfa_enabled_at     timestamptz,
  mfa_recovery_codes text[],             -- Argon2id hashes of single-use codes
  failed_login_count integer     NOT NULL DEFAULT 0,
  locked_until       timestamptz,
  last_login_at      timestamptz,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  created_by         uuid REFERENCES users (id) ON DELETE SET NULL
);
CREATE UNIQUE INDEX users_email_normalized_key ON users (email_normalized);

-- Invitations: registration is invite-only, there is no public sign-up path.
CREATE TABLE invitations (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email_normalized text        NOT NULL,
  role             user_role   NOT NULL,
  display_name     text        NOT NULL,
  token_hash       text        NOT NULL,
  expires_at       timestamptz NOT NULL,
  accepted_at      timestamptz,
  revoked_at       timestamptz,
  invited_by       uuid        NOT NULL REFERENCES users (id) ON DELETE RESTRICT,
  created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX invitations_email_idx ON invitations (email_normalized);
CREATE UNIQUE INDEX invitations_token_hash_key ON invitations (token_hash);

-- Sessions: server-side, revocable, absolute + idle expiry.
CREATE TABLE sessions (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id        uuid        NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  token_hash     text        NOT NULL,
  issued_at      timestamptz NOT NULL DEFAULT now(),
  last_seen_at   timestamptz NOT NULL DEFAULT now(),
  absolute_expires_at timestamptz NOT NULL,
  idle_expires_at     timestamptz NOT NULL,
  revoked_at     timestamptz,
  -- Recorded for audit; never used to identify a patient.
  ip             inet,
  user_agent     text,
  mfa_satisfied  boolean     NOT NULL DEFAULT false
);
CREATE UNIQUE INDEX sessions_token_hash_key ON sessions (token_hash);
CREATE INDEX sessions_user_idx ON sessions (user_id);

-- Single-use tokens for email verification and password reset.
CREATE TYPE token_purpose AS ENUM ('email_verification', 'password_reset');
CREATE TABLE auth_tokens (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id    uuid          NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  purpose    token_purpose NOT NULL,
  token_hash text          NOT NULL,
  expires_at timestamptz   NOT NULL,
  used_at    timestamptz,
  created_at timestamptz   NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX auth_tokens_token_hash_key ON auth_tokens (token_hash);
CREATE INDEX auth_tokens_user_purpose_idx ON auth_tokens (user_id, purpose);

-- Audit log: append-only. Revoking UPDATE/DELETE is enforced by role grants
-- in 0006; the application user may only INSERT and SELECT.
CREATE TABLE audit_log (
  id          bigserial PRIMARY KEY,
  occurred_at timestamptz NOT NULL DEFAULT now(),
  actor_id    uuid REFERENCES users (id) ON DELETE SET NULL,
  actor_email text,
  action      text        NOT NULL,
  entity_type text,
  entity_id   text,
  ip          inet,
  user_agent  text,
  -- Structured detail. Must never contain patient-identifiable information.
  detail      jsonb       NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX audit_log_occurred_idx ON audit_log (occurred_at DESC);
CREATE INDEX audit_log_actor_idx ON audit_log (actor_id, occurred_at DESC);
CREATE INDEX audit_log_entity_idx ON audit_log (entity_type, entity_id, occurred_at DESC);

-- Rate limiting counters, persisted so limits survive a restart and apply
-- across API replicas.
CREATE TABLE rate_limit_counters (
  bucket      text        NOT NULL,
  window_start timestamptz NOT NULL,
  count       integer     NOT NULL DEFAULT 0,
  PRIMARY KEY (bucket, window_start)
);
CREATE INDEX rate_limit_window_idx ON rate_limit_counters (window_start);
