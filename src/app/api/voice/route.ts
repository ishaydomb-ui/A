import { NextResponse } from "next/server";
import { handleIntake } from "@/lib/intake";
import { actorKey } from "@/lib/auth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Voice notes recorded in the dashboard itself.
 *
 * Separate from /api/intake because that one is for external callers (WhatsApp,
 * iOS Shortcuts) and is guarded by a shared token. This one is same-origin and
 * should sit behind the app's own session auth once Google sign-in is wired up.
 */
export async function POST(req: Request) {
  try {
    const form = await req.formData();
    const file = form.get("audio");
    if (!(file instanceof File)) {
      return NextResponse.json({ error: "audio file required" }, { status: 400 });
    }

    const reply = await handleIntake({
      audio: {
        data: Buffer.from(await file.arrayBuffer()),
        mimeType: file.type || "audio/webm",
      },
      channel: "voice",
      from: await actorKey(),
    });
    return NextResponse.json(reply);
  } catch (err) {
    return NextResponse.json({ error: (err as Error).message }, { status: 500 });
  }
}
