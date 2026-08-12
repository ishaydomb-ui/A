"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

interface Draft {
  id: number;
  to_addr: string | null;
  cc_addr: string | null;
  subject: string | null;
  body: string;
  composeUrl: string | null;
  created_at: string;
}

/**
 * Drafts waiting for a human.
 *
 * "Open in mail" is a mailto: link — it fills in the compose window and stops.
 * There is no send button here and there never will be; the whole point is that
 * the last action belongs to a person.
 */
export function DraftList({ drafts }: { drafts: Draft[] }) {
  const router = useRouter();
  const [open, setOpen] = useState<number | null>(drafts[0]?.id ?? null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState<number | null>(null);

  async function mark(id: number, status: "sent" | "discarded") {
    setBusy(true);
    await fetch("/api/drafts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, status }),
    });
    setBusy(false);
    router.refresh();
  }

  async function copy(draft: Draft) {
    await navigator.clipboard.writeText(draft.body);
    setCopied(draft.id);
    setTimeout(() => setCopied(null), 2000);
  }

  if (drafts.length === 0) {
    return (
      <div className="rounded-2xl border border-[--color-line] bg-[--color-surface] p-6 text-center text-sm text-[--color-muted]">
        No drafts waiting.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {drafts.map((d) => (
        <div key={d.id} className="rounded-2xl border border-[--color-line] bg-[--color-surface] p-4">
          <button
            onClick={() => setOpen(open === d.id ? null : d.id)}
            className="w-full text-start"
          >
            <div className="font-medium" dir="auto">
              {d.subject ?? "(no subject)"}
            </div>
            <div className="mt-0.5 text-xs text-[--color-muted]" dir="auto">
              {d.to_addr ? `To ${d.to_addr}` : "No recipient set"}
              {d.cc_addr ? ` · cc ${d.cc_addr}` : ""}
            </div>
          </button>

          {open === d.id && (
            <>
              <pre
                className="mt-3 max-h-96 overflow-auto rounded-lg bg-black/5 p-3 text-sm whitespace-pre-wrap dark:bg-white/5"
                dir="auto"
              >
                {d.body}
              </pre>

              <div className="mt-3 flex flex-wrap gap-2">
                {d.composeUrl && (
                  <a
                    href={d.composeUrl}
                    className="rounded-xl bg-[--color-accent] px-3 py-1.5 text-sm font-medium text-white"
                  >
                    Open in mail
                  </a>
                )}
                <button
                  onClick={() => copy(d)}
                  className="rounded-xl border border-[--color-line] px-3 py-1.5 text-sm"
                >
                  {copied === d.id ? "Copied" : "Copy text"}
                </button>
                <button
                  onClick={() => mark(d.id, "sent")}
                  disabled={busy}
                  className="rounded-xl border border-[--color-line] px-3 py-1.5 text-sm disabled:opacity-40"
                >
                  I sent it
                </button>
                <button
                  onClick={() => mark(d.id, "discarded")}
                  disabled={busy}
                  className="rounded-xl border border-[--color-line] px-3 py-1.5 text-sm text-[--color-muted] disabled:opacity-40"
                >
                  Discard
                </button>
              </div>
              <p className="mt-2 text-xs text-[--color-muted]">
                Nothing has been sent. &ldquo;Open in mail&rdquo; fills in your compose window —
                you press send.
              </p>
            </>
          )}
        </div>
      ))}
    </div>
  );
}
