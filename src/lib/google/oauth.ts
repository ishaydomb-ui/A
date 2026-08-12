import { one, run } from "../db";
import { encrypt, decrypt } from "../crypto";

/**
 * Google OAuth 2.0.
 *
 * One consent covers sign-in and the data scopes, so Ishay and Liran each
 * authorise once and the same grant serves both "who are you" and "read your
 * calendar". Refresh tokens are encrypted at rest with the same key as store
 * credentials and never enter a model prompt.
 */

export const SCOPES = [
  "openid",
  "email",
  "profile",
  "https://www.googleapis.com/auth/calendar.readonly",
  // Sending is restricted in code to household addresses only (see lib/mail.ts).
  // This scope cannot email anyone else, because the allowlist runs first.
  "https://www.googleapis.com/auth/gmail.send",
  // Drive and gmail.readonly go here when those syncs land; adding a scope
  // later forces a re-consent, so it is worth deciding the full set up front.
];

const AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth";
const TOKEN_URL = "https://oauth2.googleapis.com/token";
const USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo";

function config() {
  const clientId = process.env.GOOGLE_CLIENT_ID;
  const clientSecret = process.env.GOOGLE_CLIENT_SECRET;
  const redirectUri = process.env.GOOGLE_REDIRECT_URI;
  if (!clientId || !clientSecret || !redirectUri) {
    throw new Error(
      "Google sign-in is not configured. Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and GOOGLE_REDIRECT_URI.",
    );
  }
  return { clientId, clientSecret, redirectUri };
}

export function isConfigured(): boolean {
  return Boolean(process.env.GOOGLE_CLIENT_ID && process.env.GOOGLE_CLIENT_SECRET);
}

export function authorizeUrl(state: string): string {
  const { clientId, redirectUri } = config();
  const params = new URLSearchParams({
    client_id: clientId,
    redirect_uri: redirectUri,
    response_type: "code",
    scope: SCOPES.join(" "),
    // offline + consent is what actually returns a refresh token; without it
    // background syncs stop working the moment the access token expires.
    access_type: "offline",
    prompt: "consent",
    include_granted_scopes: "true",
    state,
  });
  return `${AUTH_URL}?${params}`;
}

interface TokenResponse {
  access_token: string;
  refresh_token?: string;
  expires_in: number;
  scope: string;
  id_token?: string;
}

export async function exchangeCode(code: string): Promise<TokenResponse> {
  const { clientId, clientSecret, redirectUri } = config();
  const res = await fetch(TOKEN_URL, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      code,
      client_id: clientId,
      client_secret: clientSecret,
      redirect_uri: redirectUri,
      grant_type: "authorization_code",
    }),
  });
  if (!res.ok) throw new Error(`Token exchange failed: ${res.status} ${await res.text()}`);
  return res.json();
}

export async function fetchUserInfo(accessToken: string): Promise<{ email: string; name?: string }> {
  const res = await fetch(USERINFO_URL, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!res.ok) throw new Error(`userinfo failed: ${res.status}`);
  return res.json();
}

export function storeTokens(personId: number, tokens: TokenResponse) {
  const expiresAt = new Date(Date.now() + tokens.expires_in * 1000).toISOString();
  const existing = one<{ refresh_token_enc: string | null }>(
    `SELECT refresh_token_enc FROM oauth_tokens WHERE person_id = ? AND provider = 'google'`,
    [personId],
  );

  // Google only returns a refresh token on first consent; keep the stored one
  // if this exchange did not include a new one.
  const refreshEnc = tokens.refresh_token
    ? encrypt(tokens.refresh_token)
    : (existing?.refresh_token_enc ?? null);

  run(
    `INSERT INTO oauth_tokens (person_id, provider, access_token_enc, refresh_token_enc, expires_at, scope, updated_at)
     VALUES (?, 'google', ?, ?, ?, ?, datetime('now'))
     ON CONFLICT(person_id, provider) DO UPDATE SET
       access_token_enc = excluded.access_token_enc,
       refresh_token_enc = COALESCE(excluded.refresh_token_enc, oauth_tokens.refresh_token_enc),
       expires_at = excluded.expires_at,
       scope = excluded.scope,
       updated_at = datetime('now')`,
    [personId, encrypt(tokens.access_token), refreshEnc, expiresAt, tokens.scope],
  );
}

/**
 * A usable access token for this person, refreshing transparently when the
 * stored one has expired. Returns null if they have never connected Google.
 */
export async function accessTokenFor(personId: number): Promise<string | null> {
  const row = one<{
    access_token_enc: string | null;
    refresh_token_enc: string | null;
    expires_at: string | null;
  }>(
    `SELECT access_token_enc, refresh_token_enc, expires_at FROM oauth_tokens
     WHERE person_id = ? AND provider = 'google'`,
    [personId],
  );
  if (!row) return null;

  const stillValid =
    row.access_token_enc &&
    row.expires_at &&
    new Date(row.expires_at).getTime() - Date.now() > 60_000;
  if (stillValid) return decrypt(row.access_token_enc!);

  if (!row.refresh_token_enc) return null;
  const { clientId, clientSecret } = config();
  const res = await fetch(TOKEN_URL, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      refresh_token: decrypt(row.refresh_token_enc),
      client_id: clientId,
      client_secret: clientSecret,
      grant_type: "refresh_token",
    }),
  });
  if (!res.ok) throw new Error(`Token refresh failed: ${res.status} ${await res.text()}`);

  const tokens = (await res.json()) as TokenResponse;
  storeTokens(personId, tokens);
  return tokens.access_token;
}

export function hasGoogleConnection(personId: number): boolean {
  return Boolean(
    one(`SELECT 1 FROM oauth_tokens WHERE person_id = ? AND provider = 'google'`, [personId]),
  );
}
