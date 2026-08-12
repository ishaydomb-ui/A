import { NextResponse, type NextRequest } from "next/server";
// Import from session.ts, not auth.ts: this file runs on the Edge runtime and
// must not pull SQLite into the bundle.
import { verifySession, SESSION_COOKIE } from "@/lib/session";

/**
 * Gate everything behind a session, except sign-in itself and the webhooks that
 * carry their own shared-secret auth.
 *
 * Escape hatch: if GOOGLE_CLIENT_ID is unset the app is not configured for
 * sign-in yet, so we let requests through. That keeps local development and the
 * seeded demo usable before OAuth credentials exist - but it means a deployed
 * instance MUST have Google configured or it is wide open.
 */

const PUBLIC_PATHS = [
  "/login",
  "/api/auth/google/start",
  "/api/auth/google/callback",
  "/api/auth/logout",
  // These verify a shared token themselves; a browser session is meaningless
  // for a server-to-server call.
  "/api/whatsapp",
  "/api/intake",
  "/api/cron",
];

export async function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;

  if (PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`))) {
    return NextResponse.next();
  }

  if (!process.env.GOOGLE_CLIENT_ID) return NextResponse.next();

  const person = await verifySession(req.cookies.get(SESSION_COOKIE)?.value);
  if (person) return NextResponse.next();

  if (pathname.startsWith("/api/")) {
    return NextResponse.json({ error: "not signed in" }, { status: 401 });
  }

  const login = new URL("/login", req.url);
  login.searchParams.set("next", pathname);
  return NextResponse.redirect(login);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
