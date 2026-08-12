import { one, run } from "./db";
import { ask, type AgentReply } from "./agent";

/**
 * One front door for every channel.
 *
 * A voice note dictated while driving, a WhatsApp message from Liran, and the
 * web chat box all normalise to the same thing: text + who said it + where it
 * came from. They then run through the identical agent loop, so the assistant
 * behaves the same everywhere and everything lands in the same database.
 */

export type Channel = "web" | "whatsapp" | "telegram" | "voice" | "email";

export interface IntakePayload {
  text?: string;
  audioUrl?: string;
  audio?: { data: Buffer; mimeType: string };
  channel: Channel;
  /** Phone number, email, or explicit person key. Resolved to a household member. */
  from?: string;
  conversationId?: number;
}

/**
 * Map an inbound identity to a household member. Anything unrecognised is
 * rejected upstream - this dashboard holds the family's whole life and is not
 * open to whoever finds the webhook URL.
 */
export function resolvePerson(from?: string): string | null {
  if (!from) return null;
  const normalized = from.replace(/[^\d a-zA-Z@.]/g, "").toLowerCase();

  const person = one<{ key: string }>(
    `SELECT key FROM people
     WHERE role = 'adult' AND (
       key = ? OR lower(email) = ? OR replace(replace(phone,'-',''),'+','') LIKE ?
     ) LIMIT 1`,
    [normalized, normalized, `%${normalized.slice(-9)}%`],
  );
  return person?.key ?? null;
}

/**
 * Speech-to-text.
 *
 * The Claude Messages API takes text and images, not audio, so a voice note
 * needs a transcription step before the agent sees it. This is deliberately
 * pluggable: point STT_URL at whichever service you prefer (a hosted Whisper
 * endpoint, or a local whisper.cpp server) and everything downstream is
 * unchanged. Hebrew/English code-switching is the norm here, so pick a model
 * that handles both rather than forcing a single language.
 */
export async function transcribe(audio: {
  data: Buffer;
  mimeType: string;
}): Promise<string> {
  const endpoint = process.env.STT_URL;
  if (!endpoint) {
    throw new Error(
      "Voice intake needs a transcription service. Set STT_URL (and STT_API_KEY) " +
        "to an OpenAI-compatible /audio/transcriptions endpoint.",
    );
  }

  const form = new FormData();
  form.append("file", new Blob([new Uint8Array(audio.data)], { type: audio.mimeType }), "note.ogg");
  form.append("model", process.env.STT_MODEL || "whisper-1");
  // Left unset on purpose: these voice notes mix Hebrew and English freely.

  const res = await fetch(endpoint, {
    method: "POST",
    headers: process.env.STT_API_KEY
      ? { Authorization: `Bearer ${process.env.STT_API_KEY}` }
      : undefined,
    body: form,
  });
  if (!res.ok) throw new Error(`Transcription failed: ${res.status} ${await res.text()}`);

  const data = (await res.json()) as { text?: string };
  if (!data.text) throw new Error("Transcription returned no text");
  return data.text.trim();
}

export async function handleIntake(payload: IntakePayload): Promise<
  AgentReply & { transcript?: string }
> {
  let text = payload.text?.trim() ?? "";
  let transcript: string | undefined;

  if (!text && (payload.audio || payload.audioUrl)) {
    const audio = payload.audio ?? (await fetchAudio(payload.audioUrl!));
    transcript = await transcribe(audio);
    text = transcript;
  }

  if (!text) throw new Error("Nothing to process - no text and no audio");

  const actor = resolvePerson(payload.from) ?? "ishay";

  if (transcript) {
    run(
      `INSERT INTO messages (conversation_id, role, channel, content, media_url, transcript, person_id)
       VALUES (?, 'user', ?, ?, ?, ?, (SELECT id FROM people WHERE key = ?))`,
      [
        payload.conversationId ?? null,
        payload.channel,
        text,
        payload.audioUrl ?? null,
        transcript,
        actor,
      ],
    );
  }

  const reply = await ask({
    message: text,
    conversationId: payload.conversationId,
    actor,
    channel: payload.channel,
  });

  return { ...reply, transcript };
}

async function fetchAudio(url: string): Promise<{ data: Buffer; mimeType: string }> {
  const res = await fetch(url, {
    headers: process.env.WHATSAPP_ACCESS_TOKEN
      ? { Authorization: `Bearer ${process.env.WHATSAPP_ACCESS_TOKEN}` }
      : undefined,
  });
  if (!res.ok) throw new Error(`Could not fetch audio: ${res.status}`);
  return {
    data: Buffer.from(await res.arrayBuffer()),
    mimeType: res.headers.get("content-type") ?? "audio/ogg",
  };
}
