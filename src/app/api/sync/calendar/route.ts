import { NextResponse } from "next/server";
import { currentPerson } from "@/lib/auth";
import { syncPerson, syncAll, syncStatus } from "@/lib/google/calendar";
import { safeEqual } from "@/lib/crypto";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  return NextResponse.json(syncStatus());
}

/**
 * Sync now. A signed-in person syncs their own calendars; the scheduler
 * (presenting the shared token) syncs everyone who has connected.
 */
export async function POST(req: Request) {
  try {
    const token = process.env.INTAKE_TOKEN;
    const presented = req.headers.get("x-intake-token") ?? "";
    if (token && presented && safeEqual(presented, token)) {
      return NextResponse.json(await syncAll());
    }

    const person = await currentPerson();
    if (!person) return NextResponse.json({ error: "not signed in" }, { status: 401 });

    return NextResponse.json({ [person.key]: await syncPerson(person.id) });
  } catch (err) {
    return NextResponse.json({ error: (err as Error).message }, { status: 500 });
  }
}
