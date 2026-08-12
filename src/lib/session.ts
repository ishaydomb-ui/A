/**
 * Session cookie primitives — deliberately dependency-free.
 *
 * The middleware runs on the Edge runtime, which has no filesystem and so
 * cannot load SQLite. Keeping sign/verify in their own module (Web Crypto only,
 * no database import) is what lets the exact same code guard both the edge
 * middleware and Node route handlers. Anything needing the `people` table lives
 * in auth.ts instead.
 */

export const SESSION_COOKIE = "beitenu_session";
const SESSION_DAYS = 30;
export const SESSION_MAX_AGE = SESSION_DAYS * 86400;

interface SessionPayload {
  k: string; // person key
  exp: number; // epoch seconds
}

function secret(): string {
  const value = process.env.AUTH_SECRET;
  if (!value || value.length < 32) {
    throw new Error("AUTH_SECRET must be set to a random string of 32+ chars");
  }
  return value;
}

function b64urlEncode(bytes: Uint8Array): string {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function b64urlDecode(str: string): Uint8Array {
  const padded = str.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (str.length % 4)) % 4);
  const bin = atob(padded);
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}

async function hmac(data: string): Promise<Uint8Array> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret()),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(data));
  return new Uint8Array(sig);
}

export async function signSession(personKey: string): Promise<string> {
  const payload: SessionPayload = {
    k: personKey,
    exp: Math.floor(Date.now() / 1000) + SESSION_MAX_AGE,
  };
  const body = b64urlEncode(new TextEncoder().encode(JSON.stringify(payload)));
  return `${body}.${b64urlEncode(await hmac(body))}`;
}

/** Returns the person key, or null if the cookie is missing, forged or expired. */
export async function verifySession(token: string | undefined): Promise<string | null> {
  if (!token) return null;
  const [body, sig] = token.split(".");
  if (!body || !sig) return null;

  const expected = b64urlEncode(await hmac(body));
  if (sig.length !== expected.length) return null;
  let diff = 0;
  for (let i = 0; i < sig.length; i++) diff |= sig.charCodeAt(i) ^ expected.charCodeAt(i);
  if (diff !== 0) return null;

  try {
    const payload = JSON.parse(new TextDecoder().decode(b64urlDecode(body))) as SessionPayload;
    if (payload.exp < Math.floor(Date.now() / 1000)) return null;
    return payload.k;
  } catch {
    return null;
  }
}
