import { NextResponse } from "next/server";
import { safeEqual } from "@/lib/crypto";
import { runDueAutomations } from "@/lib/automations";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Scheduler tick. Point a platform cron (Railway/Fly/Vercel Cron) at this every
 * 15 minutes; it decides which automations are actually due.
 */
export async function POST(req: Request) {
  const token = process.env.INTAKE_TOKEN;
  if (!token) return NextResponse.json({ error: "INTAKE_TOKEN not set" }, { status: 503 });
  if (!safeEqual(req.headers.get("x-intake-token") ?? "", token)) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }

  try {
    return NextResponse.json(await runDueAutomations());
  } catch (err) {
    return NextResponse.json({ error: (err as Error).message }, { status: 500 });
  }
}
