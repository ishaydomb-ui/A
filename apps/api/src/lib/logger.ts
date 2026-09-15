import { pino } from 'pino';
import { config } from '../config.js';

/**
 * Structured logging. The redact list is deliberately broad: this system must
 * never write credentials, tokens or anything patient-identifiable to a log.
 */
export const logger = pino({
  level: config.isTest ? 'silent' : config.logLevel,
  redact: {
    paths: [
      'req.headers.cookie',
      'req.headers.authorization',
      'res.headers["set-cookie"]',
      '*.password',
      '*.newPassword',
      '*.currentPassword',
      '*.token',
      '*.mfaCode',
      '*.mfa_secret',
      '*.mfaSecret',
      '*.recoveryCode',
      'password',
      'token',
    ],
    censor: '[redacted]',
  },
  base: { service: 'medcat-api' },
});
