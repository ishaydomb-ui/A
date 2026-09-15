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

/**
 * What became of a message, from the point of view of the person who caused
 * it to be sent.
 *
 * 'not_configured' and 'failed' are deliberately distinct: the first means
 * nobody expected an email, the second means one was expected and did not
 * arrive. An administrator needs to tell those apart, because only the second
 * one is a fault to chase — and in both cases the invitation link still has to
 * be delivered by hand.
 */
export type DeliveryStatus = 'sent' | 'not_configured' | 'failed';

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
  // Bounded waits. A mail host that accepts the connection and then says
  // nothing would otherwise hold the request open for minutes, and the person
  // left looking at a spinner is an administrator in the middle of inviting a
  // colleague. Ten seconds is long enough for any working server.
  const transporter = createTransport(url, {
    connectionTimeout: 10_000,
    greetingTimeout: 10_000,
    socketTimeout: 20_000,
  });
  // Ask the server up front whether it will accept us. Without this, bad
  // credentials stay invisible until the first person is invited, and the
  // symptom then shows up as a missing email rather than as a configuration
  // error — at the worst possible moment, in front of the new colleague.
  try {
    await transporter.verify();
    logger.info('smtp: the mail server accepted our credentials');
  } catch (err) {
    logger.error(
      { err: err instanceof Error ? err.message : err },
      'smtp: the mail server refused the connection or the credentials — mail will fail until this is fixed',
    );
  }
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
    // Almost every provider rejects a From address it does not recognise, and
    // the placeholder default is the one most likely to be left behind.
    if (config.mailFrom === 'no-reply@localhost') {
      logger.warn(
        'MAIL_FROM is still the placeholder — most mail servers will reject messages from it. Set it to an address the SMTP account is allowed to send as.',
      );
    }
    active = await createSmtpTransport(config.smtpUrl);
    logger.info({ from: config.mailFrom }, 'mail transport: smtp');
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

/**
 * Sends without letting the mail server decide whether the operation
 * succeeds.
 *
 * An invitation is created in the database before its email goes out, so a
 * refused SMTP connection used to fail the whole request: the account existed,
 * the token existed, and the administrator got an error page instead of the
 * link — leaving an invitation nobody could deliver. The email is a
 * convenience; the link is the thing that matters, and it is already on its
 * way back to someone who can pass it on.
 */
export async function tryDeliver(mail: Mail): Promise<DeliveryStatus> {
  if (active === outboxTransport) {
    await active.send(mail);
    return 'not_configured';
  }
  try {
    await active.send(mail);
    return 'sent';
  } catch (err) {
    logger.error(
      { err: err instanceof Error ? err.message : err, to: mail.to, subject: mail.subject },
      'mail delivery failed — the message was not sent',
    );
    return 'failed';
  }
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
