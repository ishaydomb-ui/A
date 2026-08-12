import { NextResponse } from "next/server";
import { exchangeCode, fetchUserInfo, storeTokens } from "@/lib/google/oauth";
import { personByEmail, signSession, SESSION_COOKIE, SESSION_MAX_AGE } from "@/lib/auth";
import { discoverCalendars } from "@/lib/google/calendar";
import { logActivity } from "@/lib/db";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: Request) {
  const url = new URL(req.url);
  const code = url.searchParams.get("code");
  const state = url.searchParams.get("state");
  const error = url.searchParams.get("error");

  if (error) return deny(url, `Google returned "${error}"`);
  if (!code) return deny(url, "No authorisation code was returned");

  // Verify the CSRF state we set on the way out.
  const cookieState = req.headers
    .get("cookie")
    ?.split(";")
    .map((c) => c.trim())
    .find((c) => c.startsWith("oauth_state="))
    ?.split("=")[1];
  if (!state || !cookieState || state !== cookieState) {
    return deny(url, "Sign-in state did not match. Please try again.");
  }

  try {
    const tokens = await exchangeCode(code);
    const info = await fetchUserInfo(tokens.access_token);

    // The allowlist gate. Anyone not a household adult is refused here,
    // before a session cookie is ever issued.
    const person = personByEmail(info.email);
    if (!person) {
      return deny(
        url,
        `${info.email} is not a household member. Only Ishay and Liran can sign in.`,
      );
    }

    storeTokens(person.id, tokens);

    // Populate the calendar list immediately so the first sync has something
    // to work with. Failure here shouldn't block signing in.
    try {
      await discoverCalendars(person.id);
    } catch {
      /* calendars can be discovered on the first sync instead */
    }

    logActivity({
      actor: person.key,
      action: "signed_in",
      summary: `${person.name} connected their Google account`,
    });

    const next = readCookie(req, "oauth_next") ?? "/";
    const res = NextResponse.redirect(new URL(next, url.origin));
    res.cookies.set(SESSION_COOKIE, await signSession(person.key), {
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      sameSite: "lax",
      path: "/",
      maxAge: SESSION_MAX_AGE,
    });
    res.cookies.delete("oauth_state");
    res.cookies.delete("oauth_next");
    return res;
  } catch (err) {
    return deny(url, (err as Error).message);
  }
}

function readCookie(req: Request, name: string): string | undefined {
  return req.headers
    .get("cookie")
    ?.split(";")
    .map((c) => c.trim())
    .find((c) => c.startsWith(`${name}=`))
    ?.split("=")[1];
}

function deny(url: URL, message: string) {
  const target = new URL("/login", url.origin);
  target.searchParams.set("error", message);
  return NextResponse.redirect(target);
}
