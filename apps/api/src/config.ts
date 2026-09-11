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

  /**
   * Login rate limits, per 15-minute window.
   *
   * The per-account limit is the one that stops password guessing and is
   * deliberately strict. The per-IP limit has to be far more generous: a
   * hospital site behind a single NAT gateway presents one address for every
   * clinician on it, so a low per-IP limit locks out the whole building.
   */
  RATE_LIMIT_LOGIN_PER_ACCOUNT: z.coerce.number().int().positive().default(10),
  RATE_LIMIT_LOGIN_PER_IP: z.coerce.number().int().positive().default(200),
  RATE_LIMIT_MFA_PER_IP: z.coerce.number().int().positive().default(200),
  RATE_LIMIT_RESET_PER_ACCOUNT: z.coerce.number().int().positive().default(5),
  RATE_LIMIT_RESET_PER_IP: z.coerce.number().int().positive().default(50),
  RATE_LIMIT_SEARCH_PER_MINUTE: z.coerce.number().int().positive().default(120),

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

  /**
   * Apply pending migrations at start-up.
   *
   * Off by default: with several API replicas, migrations belong in the deploy
   * step so they run once. For a single-instance deployment it removes a whole
   * class of "it starts but every request fails" confusion.
   */
  RUN_MIGRATIONS_ON_START: booleanish.default(false),

  /**
   * Looking up where a clinical claim is documented, in external registries.
   * Off by default: it makes outbound requests, which a deployment should opt
   * into rather than discover.
   */
  SOURCE_LOOKUP_ENABLED: booleanish.default(false),
  SOURCE_LOOKUP_TIMEOUT_MS: z.coerce.number().int().positive().default(8000),
  DAILYMED_BASE_URL: z.string().default('https://dailymed.nlm.nih.gov/dailymed'),
});

const parsed = schema.safeParse(process.env);
if (!parsed.success) {
  const issues = parsed.error.issues.map((i) => `  - ${i.path.join('.')}: ${i.message}`).join('\n');
  throw new Error(`Invalid environment configuration:\n${issues}`);
}
const env = parsed.data;

const DEV_PLACEHOLDERS = ['change-me', 'changeme', 'insecure', 'placeholder', 'example'];

/**
 * A loopback address is a secure context to the browser and is not reachable
 * from another machine, so running without TLS there is safe. Anywhere else,
 * an insecure cookie would travel in clear over a real network.
 */
function isLoopbackUrl(url: string): boolean {
  try {
    const { hostname, protocol } = new URL(url);
    if (protocol === 'https:') return false;
    return hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '[::1]';
  } catch {
    return false;
  }
}

const loopbackOnly = isLoopbackUrl(env.PUBLIC_URL);

if (env.NODE_ENV === 'production') {
  const lower = env.APP_SECRET.toLowerCase();
  if (DEV_PLACEHOLDERS.some((p) => lower.includes(p))) {
    throw new Error('APP_SECRET still contains a development placeholder; generate a real secret.');
  }
  if (!env.COOKIE_SECURE && !loopbackOnly) {
    throw new Error(
      'COOKIE_SECURE must be true in production. It may only be false when PUBLIC_URL is a ' +
        'loopback address (http://localhost), which is not reachable from another machine.',
    );
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

  rateLimits: {
    loginPerAccount: env.RATE_LIMIT_LOGIN_PER_ACCOUNT,
    loginPerIp: env.RATE_LIMIT_LOGIN_PER_IP,
    mfaPerIp: env.RATE_LIMIT_MFA_PER_IP,
    resetPerAccount: env.RATE_LIMIT_RESET_PER_ACCOUNT,
    resetPerIp: env.RATE_LIMIT_RESET_PER_IP,
    searchPerMinute: env.RATE_LIMIT_SEARCH_PER_MINUTE,
  },

  importStorageDir: env.IMPORT_STORAGE_DIR,
  maxUploadBytes: env.MAX_UPLOAD_BYTES,

  smtpUrl: env.SMTP_URL,
  mailFrom: env.MAIL_FROM,

  logLevel: env.LOG_LEVEL,
  trustProxy: env.TRUST_PROXY,
  runMigrationsOnStart: env.RUN_MIGRATIONS_ON_START,

  sources: {
    enabled: env.SOURCE_LOOKUP_ENABLED,
    timeoutMs: env.SOURCE_LOOKUP_TIMEOUT_MS,
    dailymedBaseUrl: env.DAILYMED_BASE_URL,
  },
  /** True when the app is served only on this machine, without TLS. */
  loopbackOnly,
} as const;

export type Config = typeof config;
