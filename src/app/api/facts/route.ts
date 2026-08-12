import { NextResponse } from "next/server";
import { rememberFact, recallFacts, forgetFact, type FactCategory } from "@/lib/facts";
import { actorKey } from "@/lib/auth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: Request) {
  const url = new URL(req.url);
  return NextResponse.json(
    recallFacts({
      query: url.searchParams.get("q") ?? undefined,
      subject: url.searchParams.get("subject") ?? undefined,
      category: url.searchParams.get("category") ?? undefined,
      limit: 500,
    }),
  );
}

export async function POST(req: Request) {
  try {
    const body = (await req.json()) as {
      subject: string;
      label: string;
      value: string;
      category?: FactCategory;
      sensitive?: boolean;
      occurred_on?: string;
      valid_until?: string;
    };
    if (!body.subject?.trim() || !body.label?.trim() || !body.value?.trim()) {
      return NextResponse.json(
        { error: "subject, label and value are required" },
        { status: 400 },
      );
    }
    return NextResponse.json(
      rememberFact({
        subject: body.subject,
        label: body.label,
        value: body.value,
        category: body.category,
        sensitive: body.sensitive,
        occurredOn: body.occurred_on,
        validUntil: body.valid_until,
        source: "manual",
        actor: await actorKey(),
      }),
    );
  } catch (err) {
    return NextResponse.json({ error: (err as Error).message }, { status: 400 });
  }
}

export async function DELETE(req: Request) {
  const id = Number(new URL(req.url).searchParams.get("id"));
  if (!id) return NextResponse.json({ error: "id required" }, { status: 400 });
  const ok = forgetFact(id, await actorKey());
  return NextResponse.json({ ok });
}
