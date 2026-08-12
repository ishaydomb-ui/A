import { all, logActivity } from "./db";
import { accessTokenFor } from "./google/oauth";
import { personByEmail } from "./auth";

/**
 * Sending — to ourselves only.
 *
 * Ishay explicitly approved two things: self-addressed digests, and emails he
 * asks for. Both are mail to the household. Everything else — the municipality,
 * the kindergarten, an insurer — still stops at a draft that a human sends.
 *
 * So the line is not "no sending", it is "no sending to anyone but us", and it
 * is enforced here in code rather than by asking the model nicely. Every
 * recipient is checked against the household adults in the `people` table, and
 * a single outside address fails the whole send. There is no override flag, no
 * "force" parameter, and no way to reach this function with an arbitrary
 * recipient.
 *
 * Transport is the sender's own Gmail account via OAuth, so the mail genuinely
 * comes from them and no separate SMTP credentials exist to leak.
 */

export class NotHouseholdError extends Error {
  constructor(addresses: string[]) {
    super(
      `Refused: ${addresses.join(", ")} ${addresses.length === 1 ? "is" : "are"} not a household ` +
        `member. This system can only email Ishay and Liran. For anyone else, write a draft.`,
    );
    this.name = "NotHouseholdError";
  }
}

/** Household adults, the only addresses that may ever receive mail from here. */
export function householdAddresses(): string[] {
  return all<{ email: string }>(
    `SELECT email FROM people WHERE role = 'adult' AND email IS NOT NULL AND email <> ''`,
  ).map((p) => p.email.toLowerCase());
}

/**
 * Throws unless every recipient is a household adult.
 * Deliberately fails the whole send rather than silently dropping outsiders —
 * quietly delivering a partial message is worse than refusing.
 */
export function assertHouseholdOnly(recipients: string[]): void {
  const allowed = new Set(householdAddresses());
  const outside = recipients
    .map((r) => extractAddress(r))
    .filter((addr) => !allowed.has(addr.toLowerCase()) && !personByEmail(addr));
  if (outside.length) throw new NotHouseholdError(outside);
}

/** "Ishay <a@b.com>" -> "a@b.com" */
function extractAddress(value: string): string {
  const match = value.match(/<([^>]+)>/);
  return (match?.[1] ?? value).trim();
}

/**
 * Header injection guard. A newline in a subject line lets an attacker append
 * arbitrary headers - including Bcc to an outside address, which would defeat
 * the allowlist entirely.
 */
function sanitizeHeader(value: string): string {
  return value.replace(/[\r\n]+/g, " ").trim();
}

export interface SendResult {
  ok: boolean;
  to: string[];
  messageId?: string;
  summary: string;
}

export async function sendToHousehold(input: {
  to?: string[];
  subject: string;
  body: string;
  actor: string;
}): Promise<SendResult> {
  // Default to everyone in the household — that's what a digest means.
  const recipients = (input.to?.length ? input.to : householdAddresses()).map(extractAddress);
  if (!recipients.length) {
    return { ok: false, to: [], summary: "No household email addresses are on file." };
  }

  // Throws before anything is composed or transmitted.
  assertHouseholdOnly(recipients);

  const sender = personByEmail(
    all<{ email: string }>(`SELECT email FROM people WHERE key = ?`, [input.actor])[0]?.email ?? "",
  );
  if (!sender) {
    return { ok: false, to: recipients, summary: `Unknown sender "${input.actor}".` };
  }

  const token = await accessTokenFor(sender.id);
  if (!token) {
    return {
      ok: false,
      to: recipients,
      summary:
        `${sender.name} has not connected Google, so there is no account to send from. ` +
        `Sign in first — and note this needs the gmail.send scope, which may mean re-consenting.`,
    };
  }

  const subject = sanitizeHeader(input.subject);
  const raw = [
    `To: ${recipients.map(sanitizeHeader).join(", ")}`,
    `Subject: =?UTF-8?B?${Buffer.from(subject, "utf8").toString("base64")}?=`,
    "MIME-Version: 1.0",
    'Content-Type: text/plain; charset="UTF-8"',
    "Content-Transfer-Encoding: base64",
    "",
    Buffer.from(input.body, "utf8").toString("base64"),
  ].join("\r\n");

  const res = await fetch("https://gmail.googleapis.com/gmail/v1/users/me/messages/send", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ raw: Buffer.from(raw, "utf8").toString("base64url") }),
  });

  if (!res.ok) {
    const detail = await res.text();
    const scopeProblem = res.status === 403 || /insufficient/i.test(detail);
    return {
      ok: false,
      to: recipients,
      summary: scopeProblem
        ? "Gmail refused the send — the account is missing the gmail.send scope. Re-consent with it enabled."
        : `Gmail send failed: ${res.status} ${detail.slice(0, 200)}`,
    };
  }

  const data = (await res.json()) as { id?: string };
  logActivity({
    actor: input.actor,
    action: "emailed_household",
    summary: `Emailed ${recipients.join(", ")}: ${subject}`,
  });

  return {
    ok: true,
    to: recipients,
    messageId: data.id,
    summary: `Sent to ${recipients.join(", ")}.`,
  };
}
