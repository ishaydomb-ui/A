import { NextResponse } from "next/server";
import { ask } from "@/lib/agent";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  try {
    const body = (await req.json()) as {
      message?: string;
      conversationId?: number;
      actor?: string;
    };
    if (!body.message?.trim()) {
      return NextResponse.json({ error: "message is required" }, { status: 400 });
    }

    const reply = await ask({
      message: body.message,
      conversationId: body.conversationId,
      actor: body.actor ?? "ishay",
      channel: "web",
    });
    return NextResponse.json(reply);
  } catch (err) {
    return NextResponse.json({ error: (err as Error).message }, { status: 500 });
  }
}
