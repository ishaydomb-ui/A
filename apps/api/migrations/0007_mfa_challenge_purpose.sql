-- Give MFA challenges their own purpose rather than borrowing the email
-- verification one, so auth_tokens stays readable in an audit.
ALTER TYPE token_purpose ADD VALUE IF NOT EXISTS 'mfa_challenge';
