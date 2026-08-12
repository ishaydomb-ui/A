"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

interface Fact {
  id: number;
  subject: string;
  label: string;
  value: string;
  sensitive: boolean;
  category: string | null;
  occurred_on: string | null;
  valid_until: string | null;
}

const CATEGORIES = [
  "identity",
  "access",
  "location",
  "medical",
  "vehicle",
  "admin",
  "contact",
  "other",
];

export function FactsList({ facts }: { facts: Fact[] }) {
  const router = useRouter();
  const [filter, setFilter] = useState("");
  const [revealed, setRevealed] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState({
    subject: "",
    label: "",
    value: "",
    category: "other",
    valid_until: "",
  });

  const visible = facts.filter((f) =>
    filter
      ? `${f.subject} ${f.label} ${f.category ?? ""}`.toLowerCase().includes(filter.toLowerCase())
      : true,
  );

  // Group by subject so "everything about the car" reads as one block.
  const grouped = visible.reduce<Record<string, Fact[]>>((acc, f) => {
    (acc[f.subject] ??= []).push(f);
    return acc;
  }, {});

  async function add() {
    if (!draft.subject.trim() || !draft.label.trim() || !draft.value.trim()) return;
    setBusy(true);
    const res = await fetch("/api/facts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...draft, valid_until: draft.valid_until || undefined }),
    });
    const data = await res.json();
    setBusy(false);
    if (data.error) {
      alert(data.error);
      return;
    }
    setDraft({ subject: "", label: "", value: "", category: "other", valid_until: "" });
    router.refresh();
  }

  async function forget(id: number) {
    if (!confirm("Delete this permanently?")) return;
    setBusy(true);
    await fetch(`/api/facts?id=${id}`, { method: "DELETE" });
    setBusy(false);
    router.refresh();
  }

  function toggle(id: number) {
    setRevealed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <div className="space-y-3">
      <div className="rounded-2xl border border-[--color-line] bg-[--color-surface] p-3">
        <div className="flex flex-wrap gap-2">
          <input
            value={draft.subject}
            onChange={(e) => setDraft({ ...draft, subject: e.target.value })}
            placeholder="About (yanai, garage, car…)"
            dir="auto"
            className="min-w-32 flex-1 rounded-lg border border-[--color-line] bg-transparent px-2 py-1.5 text-sm outline-none"
          />
          <input
            value={draft.label}
            onChange={(e) => setDraft({ ...draft, label: e.target.value })}
            placeholder="What (ID number, location…)"
            dir="auto"
            className="min-w-32 flex-1 rounded-lg border border-[--color-line] bg-transparent px-2 py-1.5 text-sm outline-none"
          />
          <input
            value={draft.value}
            onChange={(e) => setDraft({ ...draft, value: e.target.value })}
            placeholder="Value"
            dir="auto"
            className="min-w-32 flex-1 rounded-lg border border-[--color-line] bg-transparent px-2 py-1.5 text-sm outline-none"
          />
          <select
            value={draft.category}
            onChange={(e) => setDraft({ ...draft, category: e.target.value })}
            className="rounded-lg border border-[--color-line] bg-transparent px-2 py-1.5 text-sm"
          >
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
          <input
            type="date"
            value={draft.valid_until}
            onChange={(e) => setDraft({ ...draft, valid_until: e.target.value })}
            title="Expires on (optional)"
            className="rounded-lg border border-[--color-line] bg-transparent px-2 py-1.5 text-sm"
          />
          <button
            onClick={add}
            disabled={busy}
            className="rounded-lg bg-[--color-accent] px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
          >
            Remember
          </button>
        </div>
        <p className="mt-2 text-xs text-[--color-muted]">
          Identity and access values are encrypted automatically.
        </p>
      </div>

      <input
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        placeholder="Search…"
        dir="auto"
        className="w-full rounded-xl border border-[--color-line] bg-[--color-surface] px-3 py-2 text-sm outline-none"
      />

      {Object.keys(grouped).length === 0 && (
        <p className="rounded-2xl border border-[--color-line] bg-[--color-surface] p-6 text-center text-sm text-[--color-muted]">
          Nothing stored yet.
        </p>
      )}

      {Object.entries(grouped).map(([subject, items]) => (
        <section
          key={subject}
          className="rounded-2xl border border-[--color-line] bg-[--color-surface] p-4"
        >
          <h2 className="mb-2 text-sm font-semibold tracking-wide text-[--color-muted] uppercase">
            {subject}
          </h2>
          {items.map((f) => (
            <div
              key={f.id}
              className="flex items-start justify-between gap-3 border-b border-[--color-line] py-2 last:border-0"
            >
              <div className="min-w-0">
                <div className="text-sm" dir="auto">
                  {f.label}
                </div>
                <div className="mt-0.5 font-mono text-sm break-all" dir="auto">
                  {f.sensitive && !revealed.has(f.id) ? (
                    <button
                      onClick={() => toggle(f.id)}
                      className="text-[--color-muted] hover:text-[--color-accent]"
                    >
                      •••••• tap to reveal
                    </button>
                  ) : (
                    <span onClick={() => f.sensitive && toggle(f.id)}>{f.value}</span>
                  )}
                </div>
                {(f.occurred_on || f.valid_until) && (
                  <div className="mt-0.5 text-xs text-[--color-muted]">
                    {f.occurred_on && `happened ${f.occurred_on}`}
                    {f.valid_until && `renew by ${f.valid_until}`}
                  </div>
                )}
              </div>
              <button
                onClick={() => forget(f.id)}
                disabled={busy}
                className="shrink-0 text-xs text-[--color-muted] hover:text-red-600"
              >
                Delete
              </button>
            </div>
          ))}
        </section>
      ))}
    </div>
  );
}
