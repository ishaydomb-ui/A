import { mkdir, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { config } from '../config.js';
import { logger } from '../lib/logger.js';

export interface Mail {
  to: string;
  subject: string;
  text: string;
}

/**
 * Mail delivery is pluggable and defaults to an on-disk outbox.
 *
 * Nothing is sent to a real recipient unless SMTP_URL is configured, so a
 * fresh deployment can never surprise anyone with an unexpected email. The
 * outbox keeps every message so an operator can inspect or hand-deliver it.
 */
export interface Transport {
  readonly name: string;
  send(mail: Mail): Promise<void>;
}

const outboxDir = resolve(config.importStorageDir, '../outbox');

export const outboxTransport: Transport = {
  name: 'outbox',
  async send(mail) {
    await mkdir(outboxDir, { recursive: true });
    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
    const safeTo = mail.to.replace(/[^a-z0-9@._-]/gi, '_');
    const file = join(outboxDir, `${stamp}_${safeTo}.txt`);
    await writeFile(file, `To: ${mail.to}\nSubject: ${mail.subject}\n\n${mail.text}\n`, 'utf8');
    logger.info({ to: mail.to, subject: mail.subject, file }, 'mail written to outbox (SMTP not configured)');
  },
};

/** Collects messages in memory; used by tests. */
export class MemoryTransport implements Transport {
  readonly name = 'memory';
  readonly sent: Mail[] = [];
  async send(mail: Mail): Promise<void> {
    this.sent.push(mail);
  }
  lastTo(to: string): Mail | undefined {
    return [...this.sent].reverse().find((m) => m.to.toLowerCase() === to.toLowerCase());
  }
  clear(): void {
    this.sent.length = 0;
  }
}

async function createSmtpTransport(url: string): Promise<Transport> {
  const { createTransport } = await import('nodemailer');
  const transporter = createTransport(url);
  return {
    name: 'smtp',
    async send(mail) {
      await transporter.sendMail({ from: config.mailFrom, ...mail });
      logger.info({ to: mail.to, subject: mail.subject }, 'mail sent');
    },
  };
}

let active: Transport = outboxTransport;

export async function initMail(): Promise<void> {
  if (config.smtpUrl) {
    active = await createSmtpTransport(config.smtpUrl);
    logger.info('mail transport: smtp');
  } else {
    logger.warn('mail transport: outbox — invitations and resets will NOT be delivered by email');
  }
}

/** Test hook; also used to force the outbox transport in dry-run deployments. */
export function setTransport(transport: Transport): void {
  active = transport;
}

export function getTransport(): Transport {
  return active;
}

export async function sendMail(mail: Mail): Promise<void> {
  await active.send(mail);
}

export function invitationMail(to: string, displayName: string, link: string, expiresHours: number): Mail {
  return {
    to,
    subject: 'Your medication catalogue account',
    text: [
      `Hello ${displayName},`,
      '',
      'An administrator has created an account for you on the medication catalogue.',
      'Use the link below to verify your address and choose a password:',
      '',
      link,
      '',
      `This link expires in ${expiresHours} hours and can be used once.`,
      'If you were not expecting this message, you can ignore it.',
    ].join('\n'),
  };
}

export function passwordResetMail(to: string, link: string, expiresMinutes: number): Mail {
  return {
    to,
    subject: 'Reset your medication catalogue password',
    text: [
      'A password reset was requested for this address.',
      '',
      link,
      '',
      `This link expires in ${expiresMinutes} minutes and can be used once.`,
      'If you did not request it, no action is needed and your password is unchanged.',
    ].join('\n'),
  };
}
