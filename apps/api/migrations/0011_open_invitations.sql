-- Invitations that are not addressed to anyone in particular.
--
-- An administrator inviting a colleague had to know and type their email
-- address. In practice the invitation travels by hand — a WhatsApp message —
-- so the address was being asked for at the one moment nobody had it to hand,
-- and typing it on a phone is where the mistakes come from.
--
-- An open invitation carries the role and nothing else. Whoever opens the link
-- supplies their own name and address, which is the person who actually knows
-- them. The link stays single-use and time-limited, so it admits exactly one
-- account, and accepted_user_id records which one it turned out to be.

ALTER TABLE invitations ALTER COLUMN email_normalized DROP NOT NULL;
ALTER TABLE invitations ALTER COLUMN display_name     DROP NOT NULL;

ALTER TABLE invitations
  ADD COLUMN accepted_user_id uuid REFERENCES users (id) ON DELETE SET NULL;

COMMENT ON COLUMN invitations.email_normalized IS
  'The address the invitation was addressed to, or NULL for an open link whose recipient supplies their own.';
COMMENT ON COLUMN invitations.accepted_user_id IS
  'Which account claimed this invitation. For an open link this is the only record of who the link became.';
