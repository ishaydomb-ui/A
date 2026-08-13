"use client";

import { useCallback, useEffect, useRef, useState } from "react";
// The same function the server uses, so the indicator cannot lie.
import { shouldRespond } from "@/lib/room-rules";

interface Msg {
  id: number;
  role: string;
  content: string;
  speaker: string | null;
  speaker_key: string | null;
  color: string | null;
  created_at: string;
}

/**
 * The shared thread.
 *
 * Polling rather than websockets: two people on phones, a few messages an hour.
 * A three-second poll is indistinguishable from live here and needs no
 * connection handling, no reconnect logic and no server state.
 */
export function Room({ initial, me }: { initial: Msg[]; me: string }) {
  const [messages, setMessages] = useState<Msg[]>(initial);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [thinking, setThinking] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const lastId = useRef<number>(initial.at(-1)?.id ?? 0);

  const poll = useCallback(async () => {
    try {
      const res = await fetch(`/api/room?since=${lastId.current}`);
      const data = await res.json();
      if (data.messages?.length) {
        setMessages((prev) => [...prev, ...data.messages]);
        lastId.current = data.messages.at(-1).id;
      }
    } catch {
      // A dropped poll is harmless — the next one catches up.
    }
  }, []);

  useEffect(() => {
    const timer = setInterval(poll, 3000);
    return () => clearInterval(timer);
  }, [poll]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, thinking]);

  async function send(askAssistant = false) {
    const text = input.trim();
    if (!text || busy) return;
    setBusy(true);
    setInput("");
    // Only show a thinking indicator when the assistant will actually answer.
    const willAnswer = askAssistant || shouldRespond(text);
    setThinking(willAnswer);

    try {
      const res = await fetch("/api/room", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, askAssistant }),
      });
      const data = await res.json();
      if (data.posted?.length) {
        setMessages((prev) => [...prev, ...data.posted]);
        lastId.current = data.posted.at(-1).id;
      }
    } finally {
      setBusy(false);
      setThinking(false);
    }
  }

  async function summarise() {
    setBusy(true);
    setThinking(true);
    try {
      await fetch("/api/room", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ summarise: true }),
      });
      await poll();
    } finally {
      setBusy(false);
      setThinking(false);
    }
  }

  return (
    <>
      <div className="flex-1 space-y-2 overflow-y-auto pb-3">
        {messages.length === 0 && (
          <p className="text-sm text-[var(--color-muted)]">
            Nothing here yet. Start thinking out loud — the assistant stays quiet until you
            bring it in.
          </p>
        )}

        {messages.map((m) => {
          const isAssistant = m.role === "assistant";
          const isMe = m.speaker_key === me;
          return (
            <div
              key={m.id}
              className={`max-w-[85%] rounded-2xl px-3 py-2 text-sm whitespace-pre-wrap ${
                isAssistant
                  ? "border border-dashed border-[var(--color-line)] bg-[var(--color-surface)]"
                  : isMe
                    ? "ms-auto bg-[var(--color-accent)] text-white"
                    : "border border-[var(--color-line)] bg-[var(--color-surface)]"
              }`}
              dir="auto"
            >
              {!isMe && (
                <div
                  className={`mb-0.5 text-[11px] font-medium ${
                    isAssistant ? "text-[var(--color-muted)]" : ""
                  }`}
                  style={!isAssistant && m.color ? { color: m.color } : undefined}
                >
                  {isAssistant ? "assistant" : (m.speaker ?? "someone")}
                </div>
              )}
              {m.content}
            </div>
          );
        })}

        {thinking && <div className="text-sm text-[var(--color-muted)]">assistant is thinking…</div>}
        <div ref={endRef} />
      </div>

      <div className="space-y-2 border-t border-[var(--color-line)] pt-2">
        <div className="flex gap-2">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && send()}
            placeholder="Say something…"
            dir="auto"
            disabled={busy}
            className="flex-1 rounded-xl border border-[var(--color-line)] bg-[var(--color-surface)] px-3 py-2 text-sm outline-none"
          />
          <button
            onClick={() => send(false)}
            disabled={busy || !input.trim()}
            className="rounded-xl bg-[var(--color-accent)] px-3 py-2 text-sm font-medium text-white disabled:opacity-40"
          >
            Post
          </button>
          <button
            onClick={() => send(true)}
            disabled={busy || !input.trim()}
            title="Post and make the assistant answer"
            className="rounded-xl border border-[var(--color-line)] px-3 py-2 text-sm disabled:opacity-40"
          >
            Ask
          </button>
        </div>

        <button
          onClick={summarise}
          disabled={busy || messages.length === 0}
          className="text-xs text-[var(--color-accent)] hover:underline disabled:opacity-40"
        >
          Turn this discussion into tasks →
        </button>
      </div>
    </>
  );
}
