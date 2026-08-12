/**
 * Sending boundaries. Run with:  npx tsx scripts/test-no-send.ts
 *
 * Ishay approved mail to themselves - digests, and anything he asks for. So the
 * rule is not "never send", it is "never send to anyone but us", and that line
 * has to hold in code rather than in a prompt. The checks that matter are the
 * ones where something tries to get a message to an outsider anyway.
 */
// Assigned unconditionally - this file deletes the database it points at.
process.env.DATABASE_PATH = "/tmp/beitenu-test-nosend.sqlite";

import fs from "node:fs";
import path from "node:path";
for (const suffix of ["", "-wal", "-shm"]) {
  fs.rmSync(`${process.env.DATABASE_PATH}${suffix}`, { force: true });
}

import { db, all, run } from "../src/lib/db";
import { createDraft, listDrafts, markDraft, composeUrl } from "../src/lib/drafts";
import { requestApproval, getApproval, decide } from "../src/lib/approvals";
import { executeApproval } from "../src/lib/executor";
import { setFocus, activeFocus, clearFocus, sweepFocus } from "../src/lib/focus";
import { assertHouseholdOnly, householdAddresses, NotHouseholdError } from "../src/lib/mail";

db();
// The allowlist is derived from these rows. Without emails here the refusal
// checks below would pass for the wrong reason - everything is refused when
// nobody is allowed.
run(`INSERT OR IGNORE INTO people (key, name, role, email) VALUES ('ishay','Ishay','adult','ishaydomb@gmail.com')`);
run(`INSERT OR IGNORE INTO people (key, name, role, email) VALUES ('liran','Liran','adult','lirikor@gmail.com')`);
run(`INSERT OR IGNORE INTO people (key, name, role, email) VALUES ('yanai','Yanai','child',NULL)`);

let failures = 0;
function check(label: string, ok: boolean, detail?: unknown) {
  if (ok) console.log(`  ✓ ${label}`);
  else {
    failures++;
    console.log(`  ✗ ${label}`, detail ?? "");
  }
}

const iso = (days: number) =>
  new Date(Date.now() + days * 86_400_000).toISOString().slice(0, 10);

async function main() {
  console.log("\n— No sending capability exists in the source —");

  const srcFiles: string[] = [];
  (function walk(dir: string) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (/\.(ts|tsx)$/.test(entry.name)) srcFiles.push(full);
    }
  })("src");

  const sources = srcFiles.map((f) => ({ file: f, text: fs.readFileSync(f, "utf8") }));

  // There must be exactly ONE place that can transmit, so there is exactly one
  // place the allowlist has to hold.
  const senders = sources.filter((s) =>
    /messages\/send|createTransport|from ['"]nodemailer/i.test(s.text) &&
    !s.file.endsWith("mail.ts"),
  );
  check("only lib/mail.ts can transmit", senders.length === 0, senders.map((s) => s.file));

  const smtpRefs = sources.filter((s) => /process\.env\.SMTP/i.test(s.text));
  check("no SMTP credentials anywhere", smtpRefs.length === 0, smtpRefs.map((s) => s.file));

  const mail = fs.readFileSync("src/lib/mail.ts", "utf8");
  check("the allowlist runs before composing", mail.indexOf("assertHouseholdOnly") < mail.indexOf("gmail.googleapis.com"));
  check(
    "there is no override option in the send signature",
    !/\b(force|bypass|skipAllowlist|allowExternal)\s*[:?=]/i.test(mail),
  );

  const executor = fs.readFileSync("src/lib/executor.ts", "utf8");
  check("the executor keeps an outward-send refusal list", /NEVER_EXECUTE/.test(executor));

  console.log("\n— Mail to ourselves is allowed —");

  const us = householdAddresses();
  check("the household allowlist is the two of them", us.length === 2, us);
  let ok = true;
  try {
    assertHouseholdOnly(["ishaydomb@gmail.com", "lirikor@gmail.com"]);
  } catch {
    ok = false;
  }
  check("both of them pass", ok);

  let named = true;
  try {
    assertHouseholdOnly(["Liran <lirikor@gmail.com>"]);
  } catch {
    named = false;
  }
  check("a display-name address still resolves", named);

  console.log("\n— Mail to anyone else is refused —");

  const outsiders: Array<[string[], string]> = [
    [["r.shapira@education.gov.il"], "the ministry"],
    [["someone@evil.com"], "a stranger"],
    [["ishaydomb@gmail.com", "someone@evil.com"], "an outsider smuggled alongside us"],
    [["ishaydomb@gmail.com.evil.com"], "a lookalike domain"],
    [["Ishay <attacker@evil.com>"], "our name over their address"],
  ];
  for (const [recipients, what] of outsiders) {
    let refused = false;
    try {
      assertHouseholdOnly(recipients);
    } catch (err) {
      refused = err instanceof NotHouseholdError;
    }
    check(`refuses ${what}`, refused, recipients);
  }

  console.log("\n— Header injection cannot smuggle a recipient —");

  // A newline in a subject line lets an attacker append arbitrary headers.
  // "Bcc: evil@example.com" would reach an outsider while every *recipient*
  // still passed the allowlist, so sanitising headers is part of the boundary.
  const mailSrc = fs.readFileSync("src/lib/mail.ts", "utf8");
  check("headers are sanitised", /sanitizeHeader/.test(mailSrc));
  check(
    "the subject is passed through the sanitiser",
    /sanitizeHeader\(input\.subject\)/.test(mailSrc),
  );
  check(
    "recipients are sanitised too",
    /recipients\.map\(sanitizeHeader\)/.test(mailSrc),
  );
  check(
    "CR and LF are what gets stripped",
    /replace\(\/\[\\r\\n\]\+\/g/.test(mailSrc),
  );

  console.log("\n— A crafted outward 'send' approval is still refused —");

  // Simulates the dangerous case: something manages to queue a send request.
  const sneaky = requestApproval({
    kind: "send_email",
    title: "Email the municipality",
    payload: { to: ["someone@example.gov.il"], subject: "Appeal", body: "..." },
    risk: "high",
  });
  decide(sneaky.approvalId, "approved", "ishay");
  const result = await executeApproval(getApproval(sneaky.approvalId)!);
  check("approving it still does not send", result.ok === false, result);
  check(
    "and the refusal says why",
    /outside the household/i.test(result.summary),
    result.summary,
  );

  for (const kind of ["send_message", "send_whatsapp", "post", "publish"]) {
    const req = requestApproval({ kind, title: `try ${kind}`, payload: {}, risk: "high" });
    decide(req.approvalId, "approved", "ishay");
    const res = await executeApproval(getApproval(req.approvalId)!);
    check(`'${kind}' is refused too`, res.ok === false);
  }

  console.log("\n— Drafting works, and stops at a draft —");

  const draft = createDraft({
    to: ["r.shapira@education.gov.il"],
    cc: ["lirikor@gmail.com"],
    subject: "ערר על שיבוץ",
    body: "לכבוד...",
    language: "he",
  });
  check("a draft is created", draft.id > 0);
  check("it starts as 'draft', not 'sent'", draft.status === "draft", draft.status);
  check("it produces a mailto link for the human", draft.composeUrl?.startsWith("mailto:") === true);
  check(
    "the link carries the body but cannot send",
    draft.composeUrl!.includes("body=") && !/send/i.test(new URL(draft.composeUrl!).protocol),
  );
  check("it appears in the pending list", listDrafts("draft").some((d) => d.id === draft.id));

  markDraft(draft.id, "sent", "ishay");
  check("only a human can mark it sent", listDrafts("draft").length === 0);
  const logged = all<{ n: number }>(
    `SELECT COUNT(*) AS n FROM activity WHERE action = 'draft_sent_by_human' AND actor = 'ishay'`,
  )[0].n;
  check("and that is recorded as the human's action", logged === 1, logged);

  console.log("\n— Focus —");

  const party = setFocus({
    title: "Berry's birthday party",
    note: "Saturday at the park",
    entityType: "tracker",
    entityRef: "party",
    until: iso(7),
  });
  check("a focus can be pinned", activeFocus().length === 1);
  check("it points at a tracker", party.entity_ref === "party");

  setFocus({ title: "Old thing", until: iso(-2) });
  check("an expired focus never shows", activeFocus().length === 1, activeFocus().map((f) => f.title));
  check("sweeping removes it", sweepFocus() === 1);

  setFocus({ title: "House move", until: iso(30) });
  check("more than one focus is allowed", activeFocus().length === 2);
  clearFocus(party.id, "ishay");
  check("and one can be cleared", activeFocus().length === 1);

  console.log(
    failures === 0 ? `\n✅ All no-send and focus checks passed.\n` : `\n❌ ${failures} failed.\n`,
  );
  process.exit(failures === 0 ? 0 : 1);
}

main();
