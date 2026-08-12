/**
 * The system must never send anything. Run with:  npx tsx scripts/test-no-send.ts
 *
 * This is a rule, not a preference, so it is tested at the level that matters:
 * the capability should be ABSENT, not merely switched off. These checks would
 * fail if someone later added a sending path, however well-intentioned.
 */
// Assigned unconditionally - this file deletes the database it points at.
process.env.DATABASE_PATH = "/tmp/beitenu-test-nosend.sqlite";

import fs from "node:fs";
import path from "node:path";
for (const suffix of ["", "-wal", "-shm"]) {
  fs.rmSync(`${process.env.DATABASE_PATH}${suffix}`, { force: true });
}

import { db, all } from "../src/lib/db";
import { createDraft, listDrafts, markDraft, composeUrl } from "../src/lib/drafts";
import { requestApproval, getApproval, decide } from "../src/lib/approvals";
import { executeApproval } from "../src/lib/executor";
import { setFocus, activeFocus, clearFocus, sweepFocus } from "../src/lib/focus";

db();

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

  // A mail transport library would be the obvious way this rule gets broken.
  const transports = sources.filter((s) =>
    /require\(['"]nodemailer|from ['"]nodemailer|createTransport|smtp:\/\//i.test(s.text),
  );
  check("no mail transport library is used", transports.length === 0, transports.map((t) => t.file));

  const smtpRefs = sources.filter((s) => /process\.env\.SMTP/i.test(s.text));
  check("no SMTP configuration is read anywhere", smtpRefs.length === 0, smtpRefs.map((s) => s.file));

  // The Gmail send endpoint, as opposed to drafts.create which does not send.
  const gmailSend = sources.filter((s) => /gmail\.googleapis\.com.*\/messages\/send|messages\.send/i.test(s.text));
  check("no Gmail send endpoint is called", gmailSend.length === 0, gmailSend.map((s) => s.file));

  const executor = fs.readFileSync("src/lib/executor.ts", "utf8");
  check("the executor has no sendEmail function", !/function sendEmail/.test(executor));
  check("the executor keeps an explicit refusal list", /NEVER_EXECUTE/.test(executor));

  console.log("\n— A crafted 'send' approval is refused —");

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
  check("and the refusal says why", /never sends/i.test(result.summary), result.summary);

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
