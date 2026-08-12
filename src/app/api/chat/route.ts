import { NextResponse } from "next/server";
import { ask } from "@/lib/agent";
import { actorKey } from "@/lib/auth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  try {
    const body = (await req.json()) as { message?: string; conversationId?: number };
    if (!body.message?.trim()) {
      return NextResponse.json({ error: "message is required" }, { status: 400 });
    }

    // The actor comes from the session, never from the request body - otherwise
    // anything reaching this endpoint could claim to be either of them.
    const reply = await ask({
      message: body.message,
      conversationId: body.conversationId,
      actor: await actorKey(),
      channel: "web",
    });
    return NextResponse.json(reply);
  } catch (err) {
    return NextResponse.json({ error: (err as Error).message }, { status: 500 });
  }
}
