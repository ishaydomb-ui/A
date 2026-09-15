/**
 * Sends one message to an address you name, and says what happened.
 *
 *   node dist/scripts/send-test-mail.js you@example.org
 *
 * Mail configuration fails quietly by nature: the symptom is an email that
 * never arrives, noticed by the person waiting for it rather than by whoever
 * set it up. This turns that into an answer you can get in ten seconds,
 * before a real invitation depends on it.
 *
 * It sends a plainly-labelled test message, never an invitation, so running
 * it cannot create an account or hand anyone access.
 */
import { config } from '../config.js';
import { getTransport, initMail, tryDeliver } from '../services/mail.js';

async function main(): Promise<void> {
  const to = process.argv[2];
  if (!to || !to.includes('@')) {
    console.error('Usage: node dist/scripts/send-test-mail.js <address>');
    process.exit(2);
  }

  await initMail();
  const transport = getTransport().name;
  if (transport === 'outbox') {
    console.error(
      'SMTP_URL is not set, so nothing can be sent. The message below was written to the outbox instead.',
    );
  }

  const status = await tryDeliver({
    to,
    subject: 'Medication catalogue — test message',
    text: [
      'This is a test of the medication catalogue mail settings.',
      '',
      'It was sent by hand from the server and means nothing on its own —',
      'no account was created and no access was granted.',
      '',
      `From: ${config.mailFrom}`,
      `Sent: ${new Date().toISOString()}`,
    ].join('\n'),
  });

  if (status === 'sent') {
    console.log(`Sent to ${to} as ${config.mailFrom}.`);
    console.log('If it does not arrive within a minute or two, check the spam folder.');
  } else if (status === 'not_configured') {
    console.log(`Written to the outbox for ${to}. Set SMTP_URL to send it for real.`);
    process.exit(1);
  } else {
    console.error(`Could not send to ${to}. The error is in the log above.`);
    process.exit(1);
  }
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : err);
  process.exit(1);
});
