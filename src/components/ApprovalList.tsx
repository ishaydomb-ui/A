"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

interface Approval {
  id: number;
  kind: string;
  title: string;
  summary: string | null;
  risk: string;
  payload: Record<string, unknown>;
}

export function ApprovalList({ approvals }: { approvals: Approval[] }) {
  const router = useRouter();
  const [busy, setBusy] = useState<number | null>(null);
  const [result, setResult] = useState<Record<number, string>>({});
  const [open, setOpen] = useState<number | null>(null);

  async function decide(id: number, decision: "approved" | "rejected") {
    setBusy(id);
    try {
      const res = await fetch("/api/approvals", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id, decision }),
      });
      const data = await res.json();
      setResult((r) => ({
        ...r,
        [id]: data.execution?.summary ?? data.error ?? `${decision}`,
      }));
      router.refresh();
    } finally {
      setBusy(null);
    }
  }

  if (approvals.length === 0) {
    return (
      <div className="rounded-2xl border border-[var(--color-line)] bg-[var(--color-surface)] p-6 text-center text-sm text-[var(--color-muted)]">
        Nothing waiting on you.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {approvals.map((a) => (
        <div key={a.id} className="rounded-2xl border border-[var(--color-line)] bg-[var(--color-surface)] p-4">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <h3 className="font-medium" dir="auto">
                {a.title}
              </h3>
              {a.summary && (
                <p className="mt-1 text-sm text-[var(--color-muted)]" dir="auto">
                  {a.summary}
                </p>
              )}
            </div>
            <span
              className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ${
                a.risk === "high"
                  ? "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300"
                  : "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300"
              }`}
            >
              {a.risk}
            </span>
          </div>

          <button
            onClick={() => setOpen(open === a.id ? null : a.id)}
            className="mt-2 text-xs text-[var(--color-accent)] hover:underline"
          >
            {open === a.id ? "Hide" : "See exactly what will happen"}
          </button>
          {open === a.id && (
            <pre className="mt-2 max-h-64 overflow-auto rounded-lg bg-black/5 p-2 text-[11px] dark:bg-white/5">
              {JSON.stringify(a.payload, null, 2)}
            </pre>
          )}

          {result[a.id] ? (
            <p className="mt-3 rounded-lg bg-black/5 p-2 text-sm dark:bg-white/5" dir="auto">
              {result[a.id]}
            </p>
          ) : (
            <div className="mt-3 flex gap-2">
              <button
                onClick={() => decide(a.id, "approved")}
                disabled={busy === a.id}
                className="rounded-xl bg-[var(--color-accent)] px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
              >
                {busy === a.id ? "…" : "Approve"}
              </button>
              <button
                onClick={() => decide(a.id, "rejected")}
                disabled={busy === a.id}
                className="rounded-xl border border-[var(--color-line)] px-3 py-1.5 text-sm disabled:opacity-40"
              >
                Reject
              </button>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
