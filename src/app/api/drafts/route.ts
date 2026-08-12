import { NextResponse } from "next/server";
import { listDrafts, markDraft, updateDraftBody } from "@/lib/drafts";
import { actorKey } from "@/lib/auth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Note what this route does NOT have: any way to send. It lists drafts, records
 * that a human sent one, and lets them edit the text first. Transmission lives
 * in the user's own mail client, by design.
 */
export async function GET(req: Request) {
  const status = new URL(req.url).searchParams.get("status") ?? "draft";
  return NextResponse.json(listDrafts(status));
}

export async function POST(req: Request) {
  try {
    const body = (await req.json()) as {
      id: number;
      status?: "sent" | "discarded";
      draftBody?: string;
    };
    if (!body.id) return NextResponse.json({ error: "id required" }, { status: 400 });

    const actor = await actorKey();
    if (body.draftBody) return NextResponse.json(updateDraftBody(body.id, body.draftBody, actor));
    if (body.status) return NextResponse.json(markDraft(body.id, body.status, actor));

    return NextResponse.json({ error: "status or draftBody required" }, { status: 400 });
  } catch (err) {
    return NextResponse.json({ error: (err as Error).message }, { status: 400 });
  }
}
