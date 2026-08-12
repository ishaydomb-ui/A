import { NextResponse } from "next/server";
import { listApprovals, decide, getApproval } from "@/lib/approvals";
import { executeApproval } from "@/lib/executor";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: Request) {
  const status = new URL(req.url).searchParams.get("status") ?? "pending";
  return NextResponse.json(listApprovals(status));
}

export async function POST(req: Request) {
  try {
    const body = (await req.json()) as {
      id: number;
      decision: "approved" | "rejected";
      actor?: string;
    };
    if (!body.id || !body.decision) {
      return NextResponse.json({ error: "id and decision are required" }, { status: 400 });
    }

    const existing = getApproval(body.id);
    if (!existing) return NextResponse.json({ error: "not found" }, { status: 404 });
    if (existing.status !== "pending") {
      return NextResponse.json({ error: `already ${existing.status}` }, { status: 409 });
    }

    const approval = decide(body.id, body.decision, body.actor ?? "ishay");

    // Approval alone changes nothing. Execution is a separate, explicit step.
    if (body.decision === "approved" && approval) {
      const result = await executeApproval(approval);
      return NextResponse.json({ approval, execution: result });
    }
    return NextResponse.json({ approval });
  } catch (err) {
    return NextResponse.json({ error: (err as Error).message }, { status: 500 });
  }
}
