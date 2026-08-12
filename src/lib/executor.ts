import { one, run, logActivity } from "./db";
import { markDone, type Approval } from "./approvals";
import { decrypt, encrypt } from "./crypto";
import { getAdapter } from "./grocery/adapters";
import type { CartLineRequest } from "./grocery/adapters/types";

/**
 * Runs an action that a human has just approved.
 *
 * Kept separate from the agent on purpose: the model decides what to propose,
 * a human decides whether it happens, and this file is the only thing that
 * actually reaches out into the world.
 */

export interface ExecutionResult {
  ok: boolean;
  summary: string;
  detail?: unknown;
}

export async function executeApproval(approval: Approval): Promise<ExecutionResult> {
  try {
    const result = await dispatch(approval);
    markDone(approval.id, result.summary);
    return result;
  } catch (err) {
    const summary = `Failed: ${(err as Error).message}`;
    run(`UPDATE approvals SET result = ? WHERE id = ?`, [summary, approval.id]);
    logActivity({
      actor: "agent",
      action: "approval_failed",
      entityType: "approval",
      entityId: approval.id,
      summary,
    });
    return { ok: false, summary };
  }
}

/**
 * Kinds this executor must never grow a handler for.
 *
 * The household rule is that nothing is transmitted on anyone's behalf: email
 * leaves only when Ishay or Liran presses send in their own mail client. This
 * list is a tripwire — if some future change adds a sending path, or a crafted
 * approval arrives asking for one, it is refused here rather than relying on
 * the model having been told not to.
 */
const NEVER_EXECUTE = new Set([
  "send_email",
  "send_message",
  "send_whatsapp",
  "send_sms",
  "reply",
  "post",
  "publish",
]);

async function dispatch(approval: Approval): Promise<ExecutionResult> {
  if (NEVER_EXECUTE.has(approval.kind)) {
    return {
      ok: false,
      summary:
        `Refused: this system never sends anything outward. "${approval.kind}" has no ` +
        `executor by design. The content is saved as a draft — open it, check it, and send ` +
        `it yourself.`,
    };
  }

  switch (approval.kind) {
    case "fill_cart":
      return fillCart(approval);
    default:
      // Everything else is recorded as done-by-hand rather than silently ignored.
      return {
        ok: true,
        summary: `Approved "${approval.title}" - no automated executor for kind "${approval.kind}", so this is for a human to carry out.`,
      };
  }
}

// ------------------------------------------------------------------ groceries

async function fillCart(approval: Approval): Promise<ExecutionResult> {
  if (process.env.BROWSER_WORKER_ENABLED !== "1") {
    return {
      ok: false,
      summary:
        "Browser worker is disabled. Set BROWSER_WORKER_ENABLED=1 once store credentials are stored.",
    };
  }

  const payload = approval.payload as { listId: number; chain?: string };
  const chain = payload.chain ?? process.env.GROCERY_DEFAULT_CHAIN ?? "shufersal";
  const adapter = getAdapter(chain);

  if (adapter.maturity === "unsupported") {
    return {
      ok: false,
      summary: `Basket automation is not available for ${adapter.label}. The list is priced and ready to shop manually.`,
    };
  }

  const cred = one<{ username: string; secret_enc: string; session_enc: string | null }>(
    `SELECT username, secret_enc, session_enc FROM credentials WHERE service = ?`,
    [chain],
  );
  if (!cred) {
    return {
      ok: false,
      summary: `No stored credentials for ${chain}. Add them in Settings first.`,
    };
  }

  const { all } = await import("./db");
  const items = all<{ name: string; qty: number; item_code: string | null }>(
    `SELECT name, qty, item_code FROM grocery_items WHERE list_id = ? AND checked = 0`,
    [payload.listId],
  );
  const lines: CartLineRequest[] = items.map((i) => ({
    name: i.name,
    qty: i.qty,
    itemCode: i.item_code,
  }));

  const result = await adapter.fillCart(lines, {
    username: cred.username,
    password: decrypt(cred.secret_enc),
    session: cred.session_enc ? decrypt(cred.session_enc) : null,
    headless: true,
    screenshotDir: "worker/screenshots",
    onSession: (session) => {
      run(
        `UPDATE credentials SET session_enc = ?, updated_at = datetime('now') WHERE service = ?`,
        [encrypt(session), chain],
      );
    },
  });

  run(`UPDATE grocery_lists SET status = 'in_cart' WHERE id = ?`, [payload.listId]);
  logActivity({
    actor: "agent",
    action: "filled_cart",
    entityType: "grocery_list",
    entityId: payload.listId,
    summary: `Filled ${adapter.label} basket: ${result.addedCount} added, ${result.missedCount} missed`,
    detail: result,
  });

  const missed = result.lines.filter((l) => l.status !== "added").map((l) => l.requested);
  return {
    ok: true,
    summary:
      `Basket ready at ${adapter.label}: ${result.addedCount} items added` +
      (missed.length ? `, ${missed.length} not found (${missed.slice(0, 5).join(", ")})` : "") +
      `. Estimated ${result.estimatedTotal.toFixed(0)} ILS. Open the cart to review and check out.`,
    detail: result,
  };
}
