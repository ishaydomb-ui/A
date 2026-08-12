import { all, one, run, logActivity } from "./db";

/**
 * Drafting, never sending.
 *
 * Hard household rule: nothing leaves this system on its own. Email goes out
 * only when Ishay or Liran opens it and presses send, in their own mail client,
 * under their own name.
 *
 * That is enforced by absence rather than by policy. There is no transport
 * here, no SMTP configuration anywhere in the project, and no executor branch
 * that could transmit. A draft is inert text plus a link that opens the user's
 * own compose window pre-filled. Even a fully compromised prompt cannot send
 * mail, because there is no code path that sends mail.
 *
 * `status = 'sent'` is a human's note that they sent it - useful for keeping a
 * case timeline honest. Nothing in this file can set it by itself.
 */

export interface Draft {
  id: number;
  channel: string;
  to_addr: string | null;
  cc_addr: string | null;
  subject: string | null;
  body: string;
  language: string | null;
  case_id: number | null;
  status: string;
  skill_key: string | null;
  created_by: string;
  created_at: string;
}

export function createDraft(input: {
  body: string;
  to?: string[];
  cc?: string[];
  subject?: string;
  channel?: string;
  language?: string;
  caseId?: number;
  skillKey?: string;
  actor?: string;
}): Draft & { composeUrl: string | null } {
  const res = run(
    `INSERT INTO drafts (channel, to_addr, cc_addr, subject, body, language, case_id, skill_key, created_by)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    [
      input.channel ?? "email",
      input.to?.join(", ") ?? null,
      input.cc?.join(", ") ?? null,
      input.subject ?? null,
      input.body,
      input.language ?? "he",
      input.caseId ?? null,
      input.skillKey ?? null,
      input.actor ?? "agent",
    ],
  );

  const draft = getDraft(res.lastInsertRowid as number)!;
  logActivity({
    actor: input.actor ?? "agent",
    action: "drafted",
    entityType: "draft",
    entityId: draft.id,
    summary: `Drafted: ${input.subject ?? input.body.slice(0, 60)} — not sent`,
    skillKey: input.skillKey,
  });
  return { ...draft, composeUrl: composeUrl(draft) };
}

/**
 * A mailto: link. This opens the user's own mail client with the fields
 * pre-filled and the cursor in their hands — it cannot send anything by itself.
 */
export function composeUrl(draft: Draft): string | null {
  if (draft.channel !== "email") return null;
  const params = new URLSearchParams();
  if (draft.subject) params.set("subject", draft.subject);
  if (draft.cc_addr) params.set("cc", draft.cc_addr);
  params.set("body", draft.body);
  return `mailto:${encodeURIComponent(draft.to_addr ?? "")}?${params.toString()}`;
}

export function getDraft(id: number): Draft | undefined {
  return one<Draft>(`SELECT * FROM drafts WHERE id = ?`, [id]);
}

export function listDrafts(status = "draft"): Array<Draft & { composeUrl: string | null }> {
  return all<Draft>(`SELECT * FROM drafts WHERE status = ? ORDER BY created_at DESC`, [
    status,
  ]).map((d) => ({ ...d, composeUrl: composeUrl(d) }));
}

export function pendingDraftCount(): number {
  return one<{ n: number }>(`SELECT COUNT(*) AS n FROM drafts WHERE status='draft'`)?.n ?? 0;
}

/**
 * Only ever called from a human clicking in the UI. Marking a draft "sent"
 * records that a person sent it; it does not send anything.
 */
export function markDraft(
  id: number,
  status: "sent" | "discarded",
  actor: string,
): Draft | undefined {
  run(`UPDATE drafts SET status = ?, updated_at = datetime('now') WHERE id = ?`, [status, id]);
  const draft = getDraft(id);
  logActivity({
    actor,
    action: status === "sent" ? "draft_sent_by_human" : "draft_discarded",
    entityType: "draft",
    entityId: id,
    summary:
      status === "sent"
        ? `${actor} sent: ${draft?.subject ?? `draft #${id}`}`
        : `Discarded draft #${id}`,
  });

  // Keep the case timeline truthful about what actually went out.
  if (status === "sent" && draft?.case_id) {
    run(
      `INSERT INTO case_items (case_id, kind, title, body, occurred_at)
       VALUES (?, 'email', ?, ?, datetime('now'))`,
      [draft.case_id, draft.subject ?? "Email sent", draft.body.slice(0, 4000)],
    );
    run(`UPDATE cases SET chase_after = date('now', '+14 days') WHERE id = ?`, [draft.case_id]);
  }
  return draft;
}

export function updateDraftBody(id: number, body: string, actor: string): Draft | undefined {
  run(`UPDATE drafts SET body = ?, updated_at = datetime('now') WHERE id = ?`, [body, id]);
  logActivity({
    actor,
    action: "edited_draft",
    entityType: "draft",
    entityId: id,
    summary: `Edited draft #${id}`,
  });
  return getDraft(id);
}
