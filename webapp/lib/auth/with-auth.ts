/**
 * Authentication wrapper for daemon-proxying API routes.
 *
 * `withAuth` extracts the cookie id from the incoming request, looks up
 * the daemon token in the server-side `CookieStore`, and surfaces a
 * `Bearer` header that the daemon-proxy code path uses. If the cookie is
 * missing / unknown, returns a 401 envelope shaped like the daemon's
 * (`{ detail: { error, message } }`) so the browser-side error handler
 * (in `lib/grove/client.ts`) can route to /login.
 */
import { NextRequest, NextResponse } from "next/server";
import { COOKIE_NAME, sharedCookieStore } from "./cookie-store";

export interface AuthContext {
  daemonToken: string;
  sessionId: string;
  label: string;
}

/**
 * Resolve the auth context for a request. Returns either an
 * `AuthContext` ready to use or a 401 `NextResponse` the caller should
 * return immediately.
 */
export async function resolveAuth(
  req: NextRequest,
): Promise<AuthContext | NextResponse> {
  const cookieId = req.cookies.get(COOKIE_NAME)?.value;
  if (!cookieId) {
    return NextResponse.json(
      { detail: { error: "auth_missing", message: "no session cookie" } },
      { status: 401 },
    );
  }
  const entry = await sharedCookieStore().lookup(cookieId);
  if (!entry) {
    const res = NextResponse.json(
      { detail: { error: "auth_invalid", message: "session expired or revoked" } },
      { status: 401 },
    );
    // Browser should drop the stale cookie too.
    res.cookies.delete(COOKIE_NAME);
    return res;
  }
  return {
    daemonToken: entry.daemonToken,
    sessionId: entry.sessionId,
    label: entry.label,
  };
}

/** True iff the resolved value is an AuthContext (not a 401 NextResponse). */
export function isAuthOk(
  v: AuthContext | NextResponse,
): v is AuthContext {
  return !(v instanceof NextResponse);
}
