"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";

/**
 * The always-present entry point. Type or hold to talk.
 *
 * This is deliberately the first thing on the dashboard: the AI is the way you
 * use this app, and the widgets below are just the readable state it maintains.
 */
export function QuickAsk() {
  const router = useRouter();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [reply, setReply] = useState<string | null>(null);
  const [recording, setRecording] = useState(false);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  async function send(message: string) {
    if (!message.trim()) return;
    setBusy(true);
    setReply(null);
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
      });
      const data = await res.json();
      setReply(data.error ? `⚠️ ${data.error}` : data.text);
      setText("");
      // The answer usually changed something - refresh the widgets below.
      router.refresh();
    } catch (err) {
      setReply(`⚠️ ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  async function startRecording() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];
      recorder.ondataavailable = (e) => chunksRef.current.push(e.data);
      recorder.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunksRef.current, { type: "audio/webm" });
        setBusy(true);
        try {
          const form = new FormData();
          form.append("audio", blob, "note.webm");
          form.append("channel", "voice");
          const res = await fetch("/api/voice", { method: "POST", body: form });
          const data = await res.json();
          setReply(data.error ? `⚠️ ${data.error}` : `🎙️ "${data.transcript}"\n\n${data.text}`);
          router.refresh();
        } catch (err) {
          setReply(`⚠️ ${(err as Error).message}`);
        } finally {
          setBusy(false);
        }
      };
      recorder.start();
      recorderRef.current = recorder;
      setRecording(true);
    } catch {
      setReply("⚠️ Microphone unavailable.");
    }
  }

  function stopRecording() {
    recorderRef.current?.stop();
    setRecording(false);
  }

  return (
    <div className="rounded-2xl border border-[--color-line] bg-[--color-surface] p-3">
      <div className="flex items-center gap-2">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send(text)}
          placeholder="Ask or tell me anything…"
          dir="auto"
          disabled={busy}
          className="min-w-0 flex-1 bg-transparent px-2 py-2 text-sm outline-none placeholder:text-[--color-muted]"
        />
        <button
          onMouseDown={startRecording}
          onMouseUp={stopRecording}
          onTouchStart={startRecording}
          onTouchEnd={stopRecording}
          disabled={busy}
          aria-label="Hold to record a voice note"
          className={`rounded-full px-3 py-2 text-lg transition ${
            recording ? "animate-pulse bg-red-100 dark:bg-red-950" : "hover:bg-black/5 dark:hover:bg-white/5"
          }`}
        >
          🎙️
        </button>
        <button
          onClick={() => send(text)}
          disabled={busy || !text.trim()}
          className="rounded-xl bg-[--color-accent] px-3 py-2 text-sm font-medium text-white disabled:opacity-40"
        >
          {busy ? "…" : "Send"}
        </button>
      </div>

      {reply && (
        <p className="mt-3 border-t border-[--color-line] pt-3 text-sm whitespace-pre-wrap" dir="auto">
          {reply}
        </p>
      )}

      <div className="mt-2 flex flex-wrap gap-1.5">
        {[
          "Which coupons are still available?",
          "Which days am I picking up the kids this week?",
          "Plan dinners for next week",
          "How's the budget this month?",
        ].map((s) => (
          <button
            key={s}
            onClick={() => send(s)}
            disabled={busy}
            className="rounded-full border border-[--color-line] px-2.5 py-1 text-[11px] text-[--color-muted] hover:bg-black/5 disabled:opacity-40 dark:hover:bg-white/5"
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}
