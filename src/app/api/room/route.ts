import { NextResponse } from "next/server";
import { roomMessages, postToRoom, summariseRoom } from "@/lib/room";
import { actorKey } from "@/lib/auth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Both of them poll this. `since` returns only what's new, so the thread updates
 * on one phone when the other person types without re-sending the history.
 */
export async function GET(req: Request) {
  const since = Number(new URL(req.url).searchParams.get("since") ?? 0);
  return NextResponse.json({
    messages: roomMessages(since),
    me: await actorKey(),
  });
}

export async function POST(req: Request) {
  try {
    const body = (await req.json()) as {
      text?: string;
      askAssistant?: boolean;
      summarise?: boolean;
    };
    const actor = await actorKey();

    if (body.summarise) {
      return NextResponse.json({ summary: await summariseRoom(actor) });
    }
    if (!body.text?.trim()) {
      return NextResponse.json({ error: "text is required" }, { status: 400 });
    }

    return NextResponse.json(
      await postToRoom({ text: body.text, actor, askAssistant: body.askAssistant }),
    );
  } catch (err) {
    return NextResponse.json({ error: (err as Error).message }, { status: 500 });
  }
}
