import { z } from 'zod';

/**
 * All configuration comes from the environment. Nothing secret is ever
 * hard-coded, and the process refuses to start in production if a secret is
 * missing, weak, or left at a development placeholder.
 */
const booleanish = z
  .union([z.boolean(), z.string()])
  .transform((v) => (typeof v === 'boolean' ? v : ['1', 'true', 'yes', 'on'].includes(v.toLowerCase())));

const schema = z.object({
  NODE_ENV: z.enum(['development', 'test', 'production']).default('development'),
  PORT: z.coerce.number().int().positive().default(4000),
  HOST: z.string().default('0.0.0.0'),

  DATABASE_URL: z.string().min(1, 'DATABASE_URL is required'),
  DATABASE_SSL: booleanish.default(false),
  DB_POOL_MAX: z.coerce.number().int().positive().default(10),

  /** Used to derive session-token and MFA-secret encryption keys. */
  APP_SECRET: z.string().min(32, 'APP_SECRET must be at least 32 characters'),

  PUBLIC_URL: z.string().url().default('http://localhost:5173'),
  COOKIE_DOMAIN: z.string().optional(),
  COOKIE_SECURE: booleanish.default(false),

  SESSION_IDLE_MINUTES: z.coerce.number().int().positive().default(30),
  SESSION_ABSOLUTE_HOURS: z.coerce.number().int().positive().default(12),

  INVITE_EXPIRY_HOURS: z.coerce.number().int().positive().default(72),
  RESET_EXPIRY_MINUTES: z.coerce.number().int().positive().default(60),

  LOGIN_MAX_ATTEMPTS: z.coerce.number().int().positive().default(8),
  LOGIN_LOCKOUT_MINUTES: z.coerce.number().int().positive().default(15),

  IMPORT_STORAGE_DIR: z.string().default('./var/imports'),
  MAX_UPLOAD_BYTES: z.coerce.number().int().positive().default(25 * 1024 * 1024),

  /**
   * Outbound mail. When unset, messages are written to the log and to
   * IMPORT_STORAGE_DIR/../outbox so that an operator can deliver them
   * manually — the system never silently drops an invitation.
   */
  SMTP_URL: z.string().optional(),
  MAIL_FROM: z.string().default('no-reply@localhost'),

  LOG_LEVEL: z.enum(['fatal', 'error', 'warn', 'info', 'debug', 'trace', 'silent']).default('info'),
  TRUST_PROXY: booleanish.default(false),
});

const parsed = schema.safeParse(process.env);
if (!parsed.success) {
  const issues = parsed.error.issues.map((i) => `  - ${i.path.join('.')}: ${i.message}`).join('\n');
  throw new Error(`Invalid environment configuration:\n${issues}`);
}
const env = parsed.data;

const DEV_PLACEHOLDERS = ['change-me', 'changeme', 'insecure', 'placeholder', 'example'];
if (env.NODE_ENV === 'production') {
  const lower = env.APP_SECRET.toLowerCase();
  if (DEV_PLACEHOLDERS.some((p) => lower.includes(p))) {
    throw new Error('APP_SECRET still contains a development placeholder; generate a real secret.');
  }
  if (!env.COOKIE_SECURE) {
    throw new Error('COOKIE_SECURE must be true in production (the app is served over HTTPS).');
  }
}

export const config = {
  env: env.NODE_ENV,
  isProduction: env.NODE_ENV === 'production',
  isTest: env.NODE_ENV === 'test',
  port: env.PORT,
  host: env.HOST,

  databaseUrl: env.DATABASE_URL,
  dbSsl: env.DATABASE_SSL,
  dbPoolMax: env.DB_POOL_MAX,

  appSecret: env.APP_SECRET,

  publicUrl: env.PUBLIC_URL.replace(/\/$/, ''),
  cookieDomain: env.COOKIE_DOMAIN,
  cookieSecure: env.COOKIE_SECURE,

  sessionIdleMs: env.SESSION_IDLE_MINUTES * 60_000,
  sessionAbsoluteMs: env.SESSION_ABSOLUTE_HOURS * 3_600_000,

  inviteExpiryMs: env.INVITE_EXPIRY_HOURS * 3_600_000,
  resetExpiryMs: env.RESET_EXPIRY_MINUTES * 60_000,

  loginMaxAttempts: env.LOGIN_MAX_ATTEMPTS,
  loginLockoutMs: env.LOGIN_LOCKOUT_MINUTES * 60_000,

  importStorageDir: env.IMPORT_STORAGE_DIR,
  maxUploadBytes: env.MAX_UPLOAD_BYTES,

  smtpUrl: env.SMTP_URL,
  mailFrom: env.MAIL_FROM,

  logLevel: env.LOG_LEVEL,
  trustProxy: env.TRUST_PROXY,
} as const;

export type Config = typeof config;
