"use client";

import { useEffect, useRef, useState } from "react";

interface Msg {
  role: "user" | "assistant";
  text: string;
  tools?: string[];
}

export default function ChatPage() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [conversationId, setConversationId] = useState<number | undefined>();
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function send() {
    const message = input.trim();
    if (!message || busy) return;
    setMessages((m) => [...m, { role: "user", text: message }]);
    setInput("");
    setBusy(true);
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, conversationId }),
      });
      const data = await res.json();
      if (data.conversationId) setConversationId(data.conversationId);
      setMessages((m) => [
        ...m,
        { role: "assistant", text: data.error ? `⚠️ ${data.error}` : data.text, tools: data.toolsUsed },
      ]);
    } catch (err) {
      setMessages((m) => [...m, { role: "assistant", text: `⚠️ ${(err as Error).message}` }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-[calc(100dvh-8rem)] flex-col sm:h-[calc(100dvh-4rem)]">
      <h1 className="mb-3 text-2xl font-semibold">Ask</h1>

      <div className="flex-1 space-y-3 overflow-y-auto pb-4">
        {messages.length === 0 && (
          <p className="text-sm text-[--color-muted]">
            Everything routes through here — questions, instructions, or a thought you want
            captured. It reads and writes the real household data, so answers are exact.
          </p>
        )}
        {messages.map((m, i) => (
          <div
            key={i}
            className={`max-w-[85%] rounded-2xl px-3 py-2 text-sm whitespace-pre-wrap ${
              m.role === "user"
                ? "ms-auto bg-[--color-accent] text-white"
                : "border border-[--color-line] bg-[--color-surface]"
            }`}
            dir="auto"
          >
            {m.text}
            {m.tools && m.tools.length > 0 && (
              <div className="mt-1.5 text-[10px] opacity-60">used: {m.tools.join(", ")}</div>
            )}
          </div>
        ))}
        {busy && <div className="text-sm text-[--color-muted]">thinking…</div>}
        <div ref={endRef} />
      </div>

      <div className="flex gap-2 border-t border-[--color-line] pt-3">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          placeholder="Message…"
          dir="auto"
          className="flex-1 rounded-xl border border-[--color-line] bg-[--color-surface] px-3 py-2 text-sm outline-none"
        />
        <button
          onClick={send}
          disabled={busy}
          className="rounded-xl bg-[--color-accent] px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
        >
          Send
        </button>
      </div>
    </div>
  );
}
