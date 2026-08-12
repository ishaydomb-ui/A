import { NextResponse } from "next/server";
import { handleIntake, type Channel } from "@/lib/intake";
import { safeEqual } from "@/lib/crypto";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Generic intake endpoint - the one to point an iOS Shortcut at for
 * "hold the button, talk, done". Accepts either JSON text or multipart audio.
 *
 * Protected by a shared token: this endpoint can create tasks and spend the
 * household's attention, so it is not open to the internet.
 */
export async function POST(req: Request) {
  const token = process.env.INTAKE_TOKEN;
  if (!token) {
    return NextResponse.json({ error: "INTAKE_TOKEN not configured" }, { status: 503 });
  }
  const provided = req.headers.get("x-intake-token") ?? "";
  if (!safeEqual(provided, token)) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }

  try {
    const contentType = req.headers.get("content-type") ?? "";

    if (contentType.includes("multipart/form-data")) {
      const form = await req.formData();
      const file = form.get("audio");
      const from = (form.get("from") as string) ?? undefined;
      const channel = ((form.get("channel") as string) ?? "voice") as Channel;

      if (!(file instanceof File)) {
        return NextResponse.json({ error: "audio file required" }, { status: 400 });
      }
      const reply = await handleIntake({
        audio: {
          data: Buffer.from(await file.arrayBuffer()),
          mimeType: file.type || "audio/ogg",
        },
        channel,
        from,
      });
      return NextResponse.json(reply);
    }

    const body = (await req.json()) as {
      text?: string;
      audioUrl?: string;
      from?: string;
      channel?: Channel;
    };
    const reply = await handleIntake({
      text: body.text,
      audioUrl: body.audioUrl,
      from: body.from,
      channel: body.channel ?? "voice",
    });
    return NextResponse.json(reply);
  } catch (err) {
    return NextResponse.json({ error: (err as Error).message }, { status: 500 });
  }
}
