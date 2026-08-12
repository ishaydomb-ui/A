import { NextResponse } from "next/server";
import { one } from "@/lib/db";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Health check for the hosting platform.
 *
 * Deliberately touches the database rather than just returning 200: a container
 * that boots but can't reach its volume is broken, and a health check that
 * doesn't notice that is worse than none. Public by design - the platform polls
 * it without a session - so it reports liveness only, never household data.
 */
export async function GET() {
  try {
    const people = one<{ n: number }>(`SELECT COUNT(*) AS n FROM people`)?.n ?? 0;
    return NextResponse.json({
      ok: true,
      database: "reachable",
      seeded: people > 0,
      signIn: process.env.GOOGLE_CLIENT_ID ? "configured" : "open",
      agent: process.env.ANTHROPIC_API_KEY ? "configured" : "missing key",
      scheduler: process.env.ENABLE_SCHEDULER === "1" ? "on" : "off",
    });
  } catch (err) {
    return NextResponse.json(
      { ok: false, error: (err as Error).message },
      { status: 503 },
    );
  }
}
