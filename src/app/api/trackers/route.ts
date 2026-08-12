import { NextResponse } from "next/server";
import { listTrackers, queryItems, addItem, updateItem } from "@/lib/trackers";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: Request) {
  const url = new URL(req.url);
  const key = url.searchParams.get("tracker");

  if (!key) return NextResponse.json(listTrackers());

  return NextResponse.json({
    tracker: listTrackers().find((t) => t.key === key),
    items: queryItems(key, {
      availableOnly: url.searchParams.get("available") === "1",
      status: url.searchParams.get("status") ?? undefined,
    }),
  });
}

export async function POST(req: Request) {
  try {
    const body = (await req.json()) as {
      tracker: string;
      data: Record<string, unknown>;
      actor?: string;
    };
    return NextResponse.json(
      addItem(body.tracker, body.data, { actor: body.actor ?? "ishay" }),
    );
  } catch (err) {
    return NextResponse.json({ error: (err as Error).message }, { status: 400 });
  }
}

export async function PATCH(req: Request) {
  try {
    const body = (await req.json()) as {
      id: number;
      status?: string;
      data?: Record<string, unknown>;
      actor?: string;
    };
    return NextResponse.json(
      updateItem(body.id, { status: body.status, data: body.data, actor: body.actor ?? "ishay" }),
    );
  } catch (err) {
    return NextResponse.json({ error: (err as Error).message }, { status: 400 });
  }
}
