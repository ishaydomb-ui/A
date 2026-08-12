import { all, one, run, logActivity } from "./db";
import {
  parseDeliveryEmail,
  STATUS_RANK,
  type DeliveryStatus,
  type InboundEmail,
} from "./email/parse-delivery";

/**
 * What's in transit, assembled from order and shipping emails.
 *
 * The whole point is that four emails about one parcel become one row that
 * moves forward. Two rules make that work: dedupe on vendor + order reference,
 * and never let a state go backwards — a chatty "update on your order" arriving
 * after "delivered" must not reopen a closed parcel.
 */

export interface Delivery {
  id: number;
  vendor: string;
  order_ref: string | null;
  description: string | null;
  status: DeliveryStatus;
  carrier: string | null;
  tracking_url: string | null;
  ordered_at: string | null;
  expected_at: string | null;
  delivered_at: string | null;
  last_update: string;
}

const OPEN_STATES = ["ordered", "shipped", "in_transit", "ready_for_pickup"];

/**
 * Fold an email into the deliveries table.
 * Returns what happened so the caller can report honestly.
 */
export function ingestEmail(
  email: InboundEmail & { messageId?: string; receivedAt?: string },
): { action: "created" | "advanced" | "ignored" | "unchanged"; delivery?: Delivery } {
  const parsed = parseDeliveryEmail(email);
  if (!parsed) return { action: "ignored" };

  const existing = one<Delivery>(
    parsed.orderRef
      ? `SELECT * FROM deliveries WHERE vendor = ? AND order_ref = ?`
      : // Without a reference, fall back to the most recent open parcel from
        // this vendor - better than spawning a duplicate row per email.
        `SELECT * FROM deliveries WHERE vendor = ? AND status IN (${OPEN_STATES.map(() => "?").join(",")})
         ORDER BY last_update DESC LIMIT 1`,
    parsed.orderRef ? [parsed.vendor, parsed.orderRef] : [parsed.vendor, ...OPEN_STATES],
  );

  if (!existing) {
    // An email with no state at all isn't worth opening a parcel for.
    if (!parsed.status) return { action: "ignored" };

    const res = run(
      `INSERT INTO deliveries
         (vendor, order_ref, description, status, ordered_at, delivered_at, source_ref, last_update)
       VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))`,
      [
        parsed.vendor,
        parsed.orderRef,
        parsed.description,
        parsed.status,
        email.receivedAt ?? new Date().toISOString(),
        parsed.status === "delivered" ? (email.receivedAt ?? new Date().toISOString()) : null,
        email.messageId ?? null,
      ],
    );
    logActivity({
      actor: "agent",
      action: "tracked_delivery",
      entityType: "delivery",
      entityId: res.lastInsertRowid as number,
      summary: `${parsed.vendor}${parsed.orderRef ? ` #${parsed.orderRef}` : ""} — ${parsed.status}`,
    });
    return { action: "created", delivery: getDelivery(res.lastInsertRowid as number) };
  }

  // No new state, or a state that would move the parcel backwards.
  if (!parsed.status) return { action: "unchanged", delivery: existing };
  if (STATUS_RANK[parsed.status] <= STATUS_RANK[existing.status]) {
    return { action: "unchanged", delivery: existing };
  }

  run(
    `UPDATE deliveries SET status = ?, description = COALESCE(?, description),
       delivered_at = CASE WHEN ? = 'delivered' THEN ? ELSE delivered_at END,
       source_ref = ?, last_update = datetime('now')
     WHERE id = ?`,
    [
      parsed.status,
      parsed.description,
      parsed.status,
      email.receivedAt ?? new Date().toISOString(),
      email.messageId ?? null,
      existing.id,
    ],
  );
  logActivity({
    actor: "agent",
    action: "advanced_delivery",
    entityType: "delivery",
    entityId: existing.id,
    summary: `${existing.vendor}${existing.order_ref ? ` #${existing.order_ref}` : ""}: ${existing.status} → ${parsed.status}`,
  });
  return { action: "advanced", delivery: getDelivery(existing.id) };
}

export function getDelivery(id: number): Delivery | undefined {
  return one<Delivery>(`SELECT * FROM deliveries WHERE id = ?`, [id]);
}

/** The dashboard snapshot: everything still on its way, soonest activity first. */
export function pendingDeliveries(): Delivery[] {
  return all<Delivery>(
    `SELECT * FROM deliveries WHERE status IN (${OPEN_STATES.map(() => "?").join(",")})
     ORDER BY CASE status
       WHEN 'ready_for_pickup' THEN 0
       WHEN 'in_transit' THEN 1
       WHEN 'shipped' THEN 2
       ELSE 3 END, last_update DESC`,
    OPEN_STATES,
  );
}

/**
 * Parcels that have gone quiet. This is the genuinely useful bit: nobody
 * notices the order that simply never arrived.
 */
export function staleDeliveries(days = 14): Delivery[] {
  return all<Delivery>(
    `SELECT * FROM deliveries
     WHERE status IN (${OPEN_STATES.map(() => "?").join(",")})
       AND julianday('now') - julianday(last_update) >= ?
     ORDER BY last_update`,
    [...OPEN_STATES, days],
  );
}

export function recentlyDelivered(days = 14): Delivery[] {
  return all<Delivery>(
    `SELECT * FROM deliveries WHERE status = 'delivered'
       AND julianday('now') - julianday(COALESCE(delivered_at, last_update)) <= ?
     ORDER BY delivered_at DESC`,
    [days],
  );
}

export function setStatus(
  id: number,
  status: DeliveryStatus,
  actor = "agent",
): Delivery | undefined {
  run(
    `UPDATE deliveries SET status = ?, last_update = datetime('now'),
       delivered_at = CASE WHEN ? = 'delivered' THEN datetime('now') ELSE delivered_at END
     WHERE id = ?`,
    [status, status, id],
  );
  const delivery = getDelivery(id);
  logActivity({
    actor,
    action: "updated_delivery",
    entityType: "delivery",
    entityId: id,
    summary: `${delivery?.vendor ?? id} marked ${status}`,
  });
  return delivery;
}

export function recordDelivery(input: {
  vendor: string;
  orderRef?: string;
  description?: string;
  status?: DeliveryStatus;
  expectedAt?: string;
  trackingUrl?: string;
  actor?: string;
}): Delivery | undefined {
  const res = run(
    `INSERT INTO deliveries (vendor, order_ref, description, status, expected_at, tracking_url, ordered_at, source)
     VALUES (?, ?, ?, ?, ?, ?, datetime('now'), 'manual')
     ON CONFLICT(vendor, order_ref) DO UPDATE SET
       description = COALESCE(excluded.description, deliveries.description),
       expected_at = COALESCE(excluded.expected_at, deliveries.expected_at),
       tracking_url = COALESCE(excluded.tracking_url, deliveries.tracking_url),
       last_update = datetime('now')`,
    [
      input.vendor,
      input.orderRef ?? null,
      input.description ?? null,
      input.status ?? "ordered",
      input.expectedAt ?? null,
      input.trackingUrl ?? null,
    ],
  );
  logActivity({
    actor: input.actor ?? "agent",
    action: "tracked_delivery",
    entityType: "delivery",
    entityId: res.lastInsertRowid as number,
    summary: `Tracking ${input.vendor}${input.orderRef ? ` #${input.orderRef}` : ""}`,
  });
  return getDelivery(res.lastInsertRowid as number);
}
