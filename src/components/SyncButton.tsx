"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

export function SyncButton() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  async function sync() {
    setBusy(true);
    setMsg(null);
    try {
      const res = await fetch("/api/sync/calendar", { method: "POST" });
      const data = await res.json();
      if (data.error) {
        setMsg(data.error);
      } else {
        const results = Object.values(data).flat() as Array<{ upserted: number; error?: string }>;
        const total = results.reduce((s, r) => s + (r.upserted ?? 0), 0);
        const failed = results.filter((r) => r.error);
        setMsg(
          failed.length
            ? `${total} events synced, ${failed.length} calendar(s) failed: ${failed[0].error}`
            : `${total} events synced.`,
        );
        router.refresh();
      }
    } catch (err) {
      setMsg((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex items-center gap-3">
      {msg && <span className="text-xs text-[--color-muted]">{msg}</span>}
      <button
        onClick={sync}
        disabled={busy}
        className="rounded-xl bg-[--color-accent] px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
      >
        {busy ? "Syncing…" : "Sync now"}
      </button>
    </div>
  );
}
