import { all } from "@/lib/db";
import { Card, Row, Badge, formatDay } from "@/components/ui";

export const dynamic = "force-dynamic";

/**
 * Cases are live threads with the outside world - an appeal, a claim, a dispute.
 * The point is that the next action and the silence clock are always visible,
 * instead of living in someone's head between emails.
 */
export default function CasesPage() {
  const cases = all<{
    id: number;
    title: string;
    status: string;
    summary: string | null;
    reference: string | null;
    due_at: string | null;
    next_action: string | null;
    next_action_at: string | null;
    chase_after: string | null;
    item_count: number;
  }>(
    `SELECT c.*, (SELECT COUNT(*) FROM case_items ci WHERE ci.case_id = c.id) AS item_count
     FROM cases c WHERE c.status != 'closed'
     ORDER BY (c.chase_after IS NULL), c.chase_after, c.opened_at DESC`,
  );

  const isStale = (c: { chase_after: string | null }) =>
    c.chase_after && new Date(c.chase_after) <= new Date();

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-2xl font-semibold">Cases</h1>
        <p className="mt-1 text-sm text-[var(--color-muted)]">
          Open threads with the outside world, each with its next step and how long it has been
          quiet.
        </p>
      </header>

      {cases.length === 0 && (
        <div className="rounded-2xl border border-[var(--color-line)] bg-[var(--color-surface)] p-6 text-center text-sm text-[var(--color-muted)]">
          No open cases. Ask the assistant to open one when something starts.
        </div>
      )}

      {cases.map((c) => (
        <Card key={c.id} title={c.status}>
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <h3 className="font-medium" dir="auto">
                {c.title}
              </h3>
              {c.summary && (
                <p className="mt-1 text-sm text-[var(--color-muted)]" dir="auto">
                  {c.summary}
                </p>
              )}
            </div>
            {isStale(c) && <Badge tone="high">needs chasing</Badge>}
          </div>

          <div className="mt-3 space-y-1">
            {c.reference && <Row left="Reference" right={c.reference} />}
            {c.next_action && (
              <Row left={c.next_action} sub="next action" right={formatDay(c.next_action_at)} />
            )}
            {c.due_at && <Row left="Deadline" right={formatDay(c.due_at)} />}
            <Row left="Linked items" right={String(c.item_count)} />
          </div>
        </Card>
      ))}
    </div>
  );
}
