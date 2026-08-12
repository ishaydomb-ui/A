import Link from "next/link";
import { listTrackers, queryItems } from "@/lib/trackers";

export const dynamic = "force-dynamic";

export default function TrackersPage() {
  const trackers = listTrackers();

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-2xl font-semibold">Trackers</h1>
        <p className="mt-1 text-sm text-[--color-muted]">
          Each tracker is a rubric you define. To add a new one, just ask — &ldquo;start tracking
          books I want to read&rdquo; — and it appears here.
        </p>
      </header>

      <div className="grid gap-3 sm:grid-cols-2">
        {trackers.map((t) => {
          const active = queryItems(t.key, { availableOnly: true }).length;
          const total = queryItems(t.key, { status: "any" }).length;
          return (
            <Link
              key={t.key}
              href={`/trackers/${t.key}`}
              className="rounded-2xl border border-[--color-line] bg-[--color-surface] p-4 transition hover:border-[--color-accent]"
            >
              <div className="flex items-center gap-2">
                <span className="text-xl">{t.icon ?? "📋"}</span>
                <span className="font-medium">{t.name}</span>
              </div>
              <p className="mt-1 line-clamp-2 text-xs text-[--color-muted]">{t.description}</p>
              <p className="mt-2 text-xs text-[--color-muted]">
                <strong className="text-[--color-ink]">{active}</strong> available · {total} total
              </p>
            </Link>
          );
        })}
      </div>
    </div>
  );
}
