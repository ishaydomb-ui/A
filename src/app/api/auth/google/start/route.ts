import { NextResponse } from "next/server";
import { authorizeUrl, isConfigured } from "@/lib/google/oauth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: Request) {
  if (!isConfigured()) {
    return NextResponse.json(
      { error: "Google sign-in is not configured yet. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET." },
      { status: 503 },
    );
  }

  // CSRF: a random state we hand to Google and check on the way back.
  const state = crypto.randomUUID();
  const next = new URL(req.url).searchParams.get("next") ?? "/";

  const res = NextResponse.redirect(authorizeUrl(state));
  res.cookies.set("oauth_state", state, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: 600,
  });
  res.cookies.set("oauth_next", next, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: 600,
  });
  return res;
}
