"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import type { TrackerField } from "@/lib/trackers";

interface Item {
  id: number;
  status: string;
  expires_at: string | null;
  data: Record<string, unknown>;
}

/**
 * One table renders every tracker, driven by its field definitions. That's what
 * makes a new rubric free: no new component, no new route, no deploy.
 */
export function TrackerTable({
  trackerKey,
  fields,
  items,
}: {
  trackerKey: string;
  fields: TrackerField[];
  items: Item[];
}) {
  const router = useRouter();
  const [showUsed, setShowUsed] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  const visible = showUsed ? items : items.filter((i) => i.status === "active");

  async function add() {
    if (!Object.values(draft).some((v) => v.trim())) return;
    setBusy(true);
    await fetch("/api/trackers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tracker: trackerKey, data: draft }),
    });
    setDraft({});
    setBusy(false);
    router.refresh();
  }

  async function setStatus(id: number, status: string) {
    setBusy(true);
    await fetch("/api/trackers", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, status }),
    });
    setBusy(false);
    router.refresh();
  }

  const expired = (item: Item) =>
    item.expires_at && new Date(item.expires_at) < new Date(new Date().toDateString());

  return (
    <div className="space-y-3">
      {/* Quick add - the same fields the agent writes to. */}
      <div className="rounded-2xl border border-[var(--color-line)] bg-[var(--color-surface)] p-3">
        <div className="flex flex-wrap gap-2">
          {fields.map((f) => (
            <input
              key={f.name}
              value={draft[f.name] ?? ""}
              onChange={(e) => setDraft({ ...draft, [f.name]: e.target.value })}
              placeholder={f.label}
              type={f.type === "date" ? "date" : f.type === "number" || f.type === "money" ? "number" : "text"}
              dir="auto"
              className="min-w-28 flex-1 rounded-lg border border-[var(--color-line)] bg-transparent px-2 py-1.5 text-sm outline-none"
            />
          ))}
          <button
            onClick={add}
            disabled={busy}
            className="rounded-lg bg-[var(--color-accent)] px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
          >
            Add
          </button>
        </div>
      </div>

      <label className="flex items-center gap-2 text-xs text-[var(--color-muted)]">
        <input type="checkbox" checked={showUsed} onChange={(e) => setShowUsed(e.target.checked)} />
        Show used / expired / archived
      </label>

      <div className="overflow-x-auto rounded-2xl border border-[var(--color-line)] bg-[var(--color-surface)]">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--color-line)] text-left text-xs text-[var(--color-muted)]">
              {fields.map((f) => (
                <th key={f.name} className="px-3 py-2 font-medium">
                  {f.label}
                </th>
              ))}
              <th className="px-3 py-2 font-medium">Status</th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {visible.length === 0 && (
              <tr>
                <td colSpan={fields.length + 2} className="px-3 py-6 text-center text-[var(--color-muted)]">
                  Nothing here yet.
                </td>
              </tr>
            )}
            {visible.map((item) => (
              <tr
                key={item.id}
                className={`border-b border-[var(--color-line)] last:border-0 ${
                  expired(item) ? "opacity-50" : ""
                }`}
              >
                {fields.map((f) => (
                  <td key={f.name} className="px-3 py-2" dir="auto">
                    {renderValue(item.data[f.name], f)}
                  </td>
                ))}
                <td className="px-3 py-2 text-xs text-[var(--color-muted)]">
                  {expired(item) ? "expired" : item.status}
                </td>
                <td className="px-3 py-2 text-right">
                  {item.status === "active" && (
                    <button
                      onClick={() => setStatus(item.id, "used")}
                      disabled={busy}
                      className="text-xs text-[var(--color-accent)] hover:underline"
                    >
                      Mark used
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function renderValue(value: unknown, field: TrackerField) {
  if (value == null || value === "") return <span className="text-[var(--color-muted)]">—</span>;
  if (field.type === "url") {
    return (
      <a href={String(value)} target="_blank" rel="noreferrer" className="text-[var(--color-accent)] hover:underline">
        link
      </a>
    );
  }
  if (field.type === "money") return `${value} ₪`;
  return String(value);
}
