import { all } from "@/lib/db";
import { openLists } from "@/lib/grocery/list";
import { catalogueStats, CHAINS } from "@/lib/grocery/prices";
import { listAdapters } from "@/lib/grocery/adapters";
import { Card, Row, Badge, formatDay } from "@/components/ui";

export const dynamic = "force-dynamic";

export default function FoodPage() {
  const plan = all<{ plan_date: string; meal: string; title: string; cook: string | null }>(
    `SELECT mp.plan_date, mp.meal, mp.title, p.name AS cook
     FROM meal_plan mp LEFT JOIN people p ON p.id = mp.cook_id
     WHERE mp.plan_date >= date('now') AND mp.plan_date <= date('now', '+7 days')
     ORDER BY mp.plan_date, mp.meal`,
  );

  const lists = openLists() as Array<{
    id: number;
    name: string;
    chain: string;
    status: string;
    est_total: number | null;
    item_count: number;
  }>;

  const expiring = all<{ name: string; expires_at: string }>(
    `SELECT name, expires_at FROM pantry_items
     WHERE expires_at IS NOT NULL AND date(expires_at) <= date('now', '+5 days')
     ORDER BY expires_at LIMIT 10`,
  );

  const stats = catalogueStats();
  // Maturity comes from the adapter, so the badge cannot drift from reality.
  const adapters = listAdapters();

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-2xl font-semibold">Food</h1>
        <p className="mt-1 text-sm text-[var(--color-muted)]">
          Ask &ldquo;plan dinners for next week&rdquo; and the plan, the list and the prices all
          get built together.
        </p>
      </header>

      <div className="grid gap-4 sm:grid-cols-2">
        <Card title="This week's meals" empty={plan.length === 0}>
          {plan.map((m, i) => (
            <Row
              key={i}
              left={m.title}
              sub={m.cook ? `${m.meal} · ${m.cook} cooks` : m.meal}
              right={formatDay(m.plan_date)}
            />
          ))}
        </Card>

        <Card title="Using up soon" empty={expiring.length === 0}>
          {expiring.map((p, i) => (
            <Row key={i} left={p.name} right={formatDay(p.expires_at)} />
          ))}
        </Card>
      </div>

      <Card title="Shopping lists" empty={lists.length === 0}>
        {lists.map((l) => (
          <Row
            key={l.id}
            left={l.name}
            sub={`${l.item_count} items · ${l.chain}`}
            right={
              <span className="flex items-center gap-2">
                {l.est_total ? `~${Math.round(l.est_total)} ₪` : ""}
                <Badge tone={l.status === "in_cart" ? "ok" : "low"}>{l.status}</Badge>
              </span>
            }
          />
        ))}
      </Card>

      <Card title="Store integration">
        {Object.values(CHAINS).map((c) => {
          const stat = stats.find((s) => s.chain === c.key);
          const maturity = adapters.find((a) => a.key === c.key)?.maturity ?? "unsupported";
          return (
            <div key={c.key} className="border-b border-[var(--color-line)] py-2.5 last:border-0">
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-medium" dir="auto">
                  {c.label}
                </span>
                <Badge
                  tone={
                    maturity === "supported" ? "ok" : maturity === "experimental" ? "medium" : "low"
                  }
                >
                  basket: {maturity === "unsupported" ? "not implemented" : maturity}
                </Badge>
              </div>
              <p className="mt-1 text-xs text-[var(--color-muted)]">{c.notes}</p>
              <p className="mt-1 text-[11px] text-[var(--color-muted)]">
                {stat ? `${stat.n} products cached · updated ${stat.updated}` : "catalogue not synced yet"}
              </p>
            </div>
          );
        })}
      </Card>
    </div>
  );
}
