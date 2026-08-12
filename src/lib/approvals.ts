import { all, one, run, json, logActivity } from "./db";

/**
 * The approval queue is the safety valve for "act on routine things, ask for
 * the rest". Anything that spends money, sends something outward, or can't be
 * undone lands here as a concrete, reviewable payload - not a vague intention.
 */

export interface Approval {
  id: number;
  kind: string;
  title: string;
  summary: string | null;
  payload: Record<string, unknown>;
  risk: string;
  status: string;
  requested_by: string;
  skill_key: string | null;
  decided_by: string | null;
  decided_at: string | null;
  result: string | null;
  created_at: string;
}

interface Row extends Omit<Approval, "payload"> {
  payload: string;
}

export function requestApproval(input: {
  kind: string;
  title: string;
  summary?: string;
  payload: unknown;
  risk?: string;
  requestedBy?: string;
  skillKey?: string;
  expiresAt?: string;
}): { approvalId: number; status: "pending"; message: string } {
  const res = run(
    `INSERT INTO approvals (kind, title, summary, payload, risk, requested_by, skill_key, expires_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
    [
      input.kind,
      input.title,
      input.summary ?? null,
      JSON.stringify(input.payload ?? {}),
      input.risk ?? "medium",
      input.requestedBy ?? "agent",
      input.skillKey ?? null,
      input.expiresAt ?? null,
    ],
  );
  const id = res.lastInsertRowid as number;
  logActivity({
    actor: "agent",
    action: "requested_approval",
    entityType: "approval",
    entityId: id,
    summary: `Needs approval: ${input.title}`,
    detail: { kind: input.kind, risk: input.risk },
    skillKey: input.skillKey,
  });
  return {
    approvalId: id,
    status: "pending",
    message:
      `Queued for approval (#${id}): ${input.title}. ` +
      `Nothing has happened yet - it runs only once approved in the dashboard.`,
  };
}

export function listApprovals(status = "pending"): Approval[] {
  return all<Row>(
    `SELECT * FROM approvals WHERE status = ? ORDER BY
       CASE risk WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, created_at DESC`,
    [status],
  ).map((r) => ({ ...r, payload: json(r.payload, {}) }));
}

export function getApproval(id: number): Approval | undefined {
  const row = one<Row>(`SELECT * FROM approvals WHERE id = ?`, [id]);
  return row ? { ...row, payload: json(row.payload, {}) } : undefined;
}

export function decide(
  id: number,
  decision: "approved" | "rejected",
  decidedBy: string,
): Approval | undefined {
  run(
    `UPDATE approvals SET status = ?, decided_by = ?, decided_at = datetime('now')
     WHERE id = ? AND status = 'pending'`,
    [decision, decidedBy, id],
  );
  const approval = getApproval(id);
  logActivity({
    actor: decidedBy,
    action: `approval_${decision}`,
    entityType: "approval",
    entityId: id,
    summary: `${decision === "approved" ? "Approved" : "Rejected"}: ${approval?.title ?? id}`,
  });
  return approval;
}

/** Called by the executor once an approved action has actually run. */
export function markDone(id: number, result: string) {
  run(`UPDATE approvals SET status='done', result=? WHERE id=?`, [result, id]);
  logActivity({
    actor: "agent",
    action: "approval_executed",
    entityType: "approval",
    entityId: id,
    summary: `Executed approval #${id}: ${result.slice(0, 160)}`,
  });
}

export function pendingCount(): number {
  return (
    one<{ n: number }>(`SELECT COUNT(*) AS n FROM approvals WHERE status='pending'`)?.n ?? 0
  );
}
