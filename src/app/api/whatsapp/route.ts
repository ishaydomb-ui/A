import { NextResponse } from "next/server";
import { handleIntake, resolvePerson } from "@/lib/intake";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * WhatsApp Cloud API webhook.
 *
 * Text and voice notes both land here. A voice note becomes a media id, which we
 * exchange for a URL, transcribe, and then run through the same agent as the web
 * chat - so "add milk to the list" works identically from the car.
 *
 * Only numbers belonging to household adults are accepted. Anything else is
 * acknowledged (so WhatsApp stops retrying) and dropped.
 */

export async function GET(req: Request) {
  // Meta's webhook verification handshake.
  const url = new URL(req.url);
  const mode = url.searchParams.get("hub.mode");
  const token = url.searchParams.get("hub.verify_token");
  const challenge = url.searchParams.get("hub.challenge");

  if (mode === "subscribe" && token && token === process.env.WHATSAPP_VERIFY_TOKEN) {
    return new NextResponse(challenge ?? "", { status: 200 });
  }
  return NextResponse.json({ error: "verification failed" }, { status: 403 });
}

interface WhatsAppMessage {
  from: string;
  type: string;
  text?: { body: string };
  audio?: { id: string };
  voice?: { id: string };
}

export async function POST(req: Request) {
  try {
    const body = await req.json();
    const messages: WhatsAppMessage[] =
      body?.entry?.[0]?.changes?.[0]?.value?.messages ?? [];

    for (const msg of messages) {
      const actor = resolvePerson(msg.from);
      if (!actor) continue; // not a household member - ignore silently

      let reply;
      if (msg.type === "text" && msg.text?.body) {
        reply = await handleIntake({ text: msg.text.body, channel: "whatsapp", from: msg.from });
      } else if (msg.type === "audio" || msg.type === "voice") {
        const mediaId = msg.audio?.id ?? msg.voice?.id;
        if (!mediaId) continue;
        const audioUrl = await resolveMediaUrl(mediaId);
        reply = await handleIntake({ audioUrl, channel: "whatsapp", from: msg.from });
      } else {
        continue;
      }

      await sendWhatsApp(msg.from, reply.text);
    }

    // Always 200 - a non-200 makes Meta retry the same message repeatedly.
    return NextResponse.json({ ok: true });
  } catch (err) {
    console.error("whatsapp webhook error", err);
    return NextResponse.json({ ok: true });
  }
}

async function resolveMediaUrl(mediaId: string): Promise<string> {
  const res = await fetch(`https://graph.facebook.com/v21.0/${mediaId}`, {
    headers: { Authorization: `Bearer ${process.env.WHATSAPP_ACCESS_TOKEN}` },
  });
  if (!res.ok) throw new Error(`media lookup failed: ${res.status}`);
  const data = (await res.json()) as { url: string };
  return data.url;
}

async function sendWhatsApp(to: string, text: string) {
  const phoneId = process.env.WHATSAPP_PHONE_NUMBER_ID;
  if (!phoneId || !text) return;
  await fetch(`https://graph.facebook.com/v21.0/${phoneId}/messages`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${process.env.WHATSAPP_ACCESS_TOKEN}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      messaging_product: "whatsapp",
      to,
      type: "text",
      text: { body: text.slice(0, 4000) },
    }),
  }).catch((err) => console.error("whatsapp send failed", err));
}
