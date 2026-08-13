import Link from "next/link";
import { all } from "@/lib/db";
import { today, upcoming } from "@/lib/schedule";
import { listApprovals } from "@/lib/approvals";
import { queryItems, listTrackers } from "@/lib/trackers";
import { pendingDeliveries, staleDeliveries } from "@/lib/deliveries";
import { Card, Row, Badge, formatDay, formatTime } from "@/components/ui";
import { QuickAsk } from "@/components/QuickAsk";
import { FocusBanner } from "@/components/FocusBanner";
import { listDrafts } from "@/lib/drafts";

export const dynamic = "force-dynamic";

/**
 * "Today" - the one screen that answers "what do I need to know right now".
 * Everything on it is a live query, never a cached summary.
 */
export default function TodayPage() {
  const todayEvents = today();
  const week = upcoming(7);
  const approvals = listApprovals("pending");

  const dueTasks = all<{ id: number; title: string; due_at: string; priority: string }>(
    `SELECT id, title, due_at, priority FROM tasks
     WHERE status = 'open' AND (due_at IS NULL OR date(due_at) <= date('now', '+2 days'))
     ORDER BY (due_at IS NULL), due_at LIMIT 8`,
  );

  const staleCases = all<{ id: number; title: string; reference: string; chase_after: string }>(
    `SELECT id, title, reference, chase_after FROM cases
     WHERE status IN ('open','waiting')
       AND (chase_after IS NULL OR date(chase_after) <= date('now'))
     ORDER BY (chase_after IS NULL), chase_after LIMIT 5`,
  );

  const expiringSoon = listTrackers().flatMap((t) =>
    t.behaviors.expireField
      ? queryItems(t.key, { expiringWithinDays: t.behaviors.notifyBeforeDays ?? 14 }).map((i) => ({
          tracker: t.name,
          label: String(i.data[t.fields[0].name] ?? "item"),
          expires: i.expires_at,
        }))
      : [],
  );

  const tonight = all<{ title: string; meal: string }>(
    `SELECT title, meal FROM meal_plan WHERE plan_date = date('now')`,
  );

  const drafts = listDrafts('draft');
  const parcels = pendingDeliveries();
  const stuck = new Set(staleDeliveries(14).map((d) => d.id));

  const activity = all<{ summary: string; actor: string; created_at: string }>(
    `SELECT summary, actor, created_at FROM activity ORDER BY id DESC LIMIT 6`,
  );

  return (
    <div className="space-y-4">
      <header className="flex items-baseline justify-between">
        <h1 className="text-2xl font-semibold">Today</h1>
        <span className="text-sm text-[var(--color-muted)]">
          {new Date().toLocaleDateString("en-GB", {
            weekday: "long",
            day: "numeric",
            month: "long",
          })}
        </span>
      </header>

      <FocusBanner />

      <QuickAsk />

      {drafts.length > 0 && (
        <Card
          title={`Drafts to send (${drafts.length})`}
          action={{ href: "/approvals", label: "Open" }}
        >
          {drafts.slice(0, 3).map((d) => (
            <Row
              key={d.id}
              left={d.subject ?? "(no subject)"}
              sub={d.to_addr ?? undefined}
              right="not sent"
            />
          ))}
        </Card>
      )}

      {approvals.length > 0 && (
        <Card title={`Waiting on you (${approvals.length})`} action={{ href: "/approvals", label: "Review" }}>
          {approvals.slice(0, 3).map((a) => (
            <Row
              key={a.id}
              left={a.title}
              sub={a.summary}
              right={<Badge tone={a.risk}>{a.risk}</Badge>}
            />
          ))}
        </Card>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <Card title="Today's schedule" empty={todayEvents.length === 0}>
          {todayEvents.map((e) => (
            <Row
              key={e.id}
              left={e.title}
              sub={[e.owner_name && `→ ${e.owner_name}`, e.location].filter(Boolean).join(" · ")}
              right={e.all_day ? "all day" : formatTime(e.starts_at)}
            />
          ))}
        </Card>

        <Card title="Due soon" empty={dueTasks.length === 0}>
          {dueTasks.map((t) => (
            <Row
              key={t.id}
              left={t.title}
              right={
                t.priority === "urgent" || t.priority === "high" ? (
                  <Badge tone="high">{t.priority}</Badge>
                ) : (
                  formatDay(t.due_at)
                )
              }
            />
          ))}
        </Card>

        <Card title="Open cases" action={{ href: "/cases", label: "All" }} empty={staleCases.length === 0}>
          {staleCases.map((c) => (
            <Row
              key={c.id}
              left={c.title}
              sub={c.reference ? `Ref ${c.reference}` : undefined}
              right={c.chase_after ? <Badge tone="medium">chase</Badge> : undefined}
            />
          ))}
        </Card>

        <Card title="Expiring soon" empty={expiringSoon.length === 0}>
          {expiringSoon.slice(0, 6).map((item, i) => (
            <Row
              key={i}
              left={item.label}
              sub={item.tracker}
              right={formatDay(item.expires)}
            />
          ))}
        </Card>

        <Card title="Tonight" empty={tonight.length === 0}>
          {tonight.map((m, i) => (
            <Row key={i} left={m.title} sub={m.meal} />
          ))}
          <Link href="/food" className="mt-2 inline-block text-xs text-[var(--color-accent)] hover:underline">
            Plan the week →
          </Link>
        </Card>

        <Card title="On its way" empty={parcels.length === 0}>
          {parcels.slice(0, 6).map((d) => (
            <Row
              key={d.id}
              left={d.vendor}
              sub={d.description ?? (d.order_ref ? `#${d.order_ref}` : undefined)}
              right={
                d.status === "ready_for_pickup" ? (
                  <Badge tone="ok">collect</Badge>
                ) : stuck.has(d.id) ? (
                  <Badge tone="high">no update</Badge>
                ) : (
                  d.status.replace(/_/g, " ")
                )
              }
            />
          ))}
        </Card>

        <Card title="Recent activity" empty={activity.length === 0}>
          {activity.map((a, i) => (
            <Row key={i} left={a.summary} right={a.actor} />
          ))}
        </Card>
      </div>

      <Card title="Next 7 days" empty={week.length === 0}>
        {week.slice(0, 12).map((e) => (
          <Row
            key={e.id}
            left={e.title}
            sub={e.subject_name ?? undefined}
            right={`${formatDay(e.starts_at)}${e.all_day ? "" : ` ${formatTime(e.starts_at)}`}`}
          />
        ))}
      </Card>
    </div>
  );
}
