import { NextResponse } from "next/server";
import { SESSION_COOKIE } from "@/lib/auth";

export const runtime = "nodejs";

export async function POST(req: Request) {
  const res = NextResponse.redirect(new URL("/login", new URL(req.url).origin));
  res.cookies.delete(SESSION_COOKIE);
  return res;
}
