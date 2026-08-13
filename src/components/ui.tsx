import Link from "next/link";

export function Card({
  title,
  action,
  children,
  empty,
}: {
  title: string;
  action?: { href: string; label: string };
  children: React.ReactNode;
  empty?: boolean;
}) {
  return (
    <section className="rounded-2xl border border-[var(--color-line)] bg-[var(--color-surface)] p-4">
      <header className="mb-3 flex items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold tracking-wide text-[var(--color-muted)] uppercase">
          {title}
        </h2>
        {action && (
          <Link href={action.href} className="text-xs text-[var(--color-accent)] hover:underline">
            {action.label}
          </Link>
        )}
      </header>
      {empty ? <p className="text-sm text-[var(--color-muted)]">Nothing here.</p> : children}
    </section>
  );
}

export function Row({
  left,
  right,
  sub,
}: {
  left: React.ReactNode;
  right?: React.ReactNode;
  sub?: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-[var(--color-line)] py-2 last:border-0">
      <div className="min-w-0">
        <div className="truncate text-sm" dir="auto">
          {left}
        </div>
        {sub && <div className="mt-0.5 text-xs text-[var(--color-muted)]">{sub}</div>}
      </div>
      {right && <div className="shrink-0 text-xs text-[var(--color-muted)]">{right}</div>}
    </div>
  );
}

const badgeTones: Record<string, string> = {
  high: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300",
  medium: "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
  low: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300",
  ok: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
};

export function Badge({ tone = "low", children }: { tone?: string; children: React.ReactNode }) {
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${badgeTones[tone] ?? badgeTones.low}`}
    >
      {children}
    </span>
  );
}

export function formatDay(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00` : iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}
