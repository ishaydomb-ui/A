import {
  createCipheriv,
  createDecipheriv,
  createHmac,
  hkdfSync,
  randomBytes,
  randomInt,
  timingSafeEqual,
} from 'node:crypto';
import { hash as argonHash, verify as argonVerify, Algorithm } from '@node-rs/argon2';
import { config } from '../config.js';

/**
 * Key material is derived from APP_SECRET with HKDF, so each purpose gets an
 * independent key and the master secret is never used directly.
 */
function deriveKey(purpose: string, bytes = 32): Buffer {
  return Buffer.from(hkdfSync('sha256', config.appSecret, 'medcat-salt', purpose, bytes));
}

const TOKEN_HMAC_KEY = deriveKey('token-hmac');
const MFA_ENC_KEY = deriveKey('mfa-encryption');

/** OWASP-aligned Argon2id parameters (19 MiB, t=2, p=1). */
const ARGON_OPTIONS = {
  algorithm: Algorithm.Argon2id,
  memoryCost: 19456,
  timeCost: 2,
  parallelism: 1,
} as const;

export function hashPassword(password: string): Promise<string> {
  return argonHash(password, ARGON_OPTIONS);
}

export async function verifyPassword(storedHash: string, password: string): Promise<boolean> {
  try {
    return await argonVerify(storedHash, password);
  } catch {
    // A malformed or truncated hash must read as "wrong password", never crash.
    return false;
  }
}

/**
 * Bearer-style secrets (session, invitation, reset tokens) are random and
 * high-entropy, so a keyed HMAC is the right verifier: constant time, no salt
 * needed, and it makes a stolen database dump useless without APP_SECRET.
 */
export function hashToken(token: string): string {
  return createHmac('sha256', TOKEN_HMAC_KEY).update(token).digest('hex');
}

export function generateToken(bytes = 32): string {
  return randomBytes(bytes).toString('base64url');
}

export function constantTimeEquals(a: string, b: string): boolean {
  const ab = Buffer.from(a);
  const bb = Buffer.from(b);
  if (ab.length !== bb.length) return false;
  return timingSafeEqual(ab, bb);
}

/** AES-256-GCM, used to keep TOTP secrets unusable in a database dump. */
export function encryptSecret(plaintext: string): string {
  const iv = randomBytes(12);
  const cipher = createCipheriv('aes-256-gcm', MFA_ENC_KEY, iv);
  const enc = Buffer.concat([cipher.update(plaintext, 'utf8'), cipher.final()]);
  const tag = cipher.getAuthTag();
  return `v1.${iv.toString('base64url')}.${tag.toString('base64url')}.${enc.toString('base64url')}`;
}

export function decryptSecret(payload: string): string {
  const [version, ivPart, tagPart, dataPart] = payload.split('.');
  if (version !== 'v1' || !ivPart || !tagPart || !dataPart) {
    throw new Error('Malformed encrypted secret');
  }
  const decipher = createDecipheriv('aes-256-gcm', MFA_ENC_KEY, Buffer.from(ivPart, 'base64url'));
  decipher.setAuthTag(Buffer.from(tagPart, 'base64url'));
  return Buffer.concat([
    decipher.update(Buffer.from(dataPart, 'base64url')),
    decipher.final(),
  ]).toString('utf8');
}

/** Human-transcribable recovery codes, avoiding ambiguous characters. */
const RECOVERY_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
export function generateRecoveryCode(): string {
  const pick = () =>
    Array.from({ length: 5 }, () => RECOVERY_ALPHABET[randomInt(RECOVERY_ALPHABET.length)]).join('');
  return `${pick()}-${pick()}`;
}
