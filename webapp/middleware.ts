import { NextRequest, NextResponse } from "next/server";

const cookieName = "grove_session";

/**
 * Everything reachable WITHOUT a session, exact-matched.
 *
 * This is the app's outermost gate — it fronts every request, including the API
 * routes — so a page that is public has to be listed here as well as being
 * public further in. `/api/version` is the case that proved it: the route
 * itself needs no auth and the BFF's own gate was deliberately left alone, but
 * this redirected it to `/login`, so the login footer asked for the daemon's
 * version and was handed the login page's HTML.
 *
 * Keep additions here rather than inventing a second mechanism: one list of
 * public paths is auditable, and a security-relevant exemption scattered across
 * files is not. Prefix-matched families (`/api/auth/**`, the pairing endpoints)
 * are handled below.
 */
const publicPaths = new Set(["/login", "/api/version"]);

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|manifest.webmanifest|.*\\..*).*)"],
};

export function middleware(request: NextRequest): NextResponse {
  const pathname = request.nextUrl.pathname;
  if (publicPaths.has(pathname) || pathname.startsWith("/api/auth/") || pathname.startsWith("/_next/")) return NextResponse.next();
  if (request.cookies.get(cookieName)?.value) return NextResponse.next();

  const redirect = request.nextUrl.clone();
  redirect.pathname = "/login";
  redirect.search = `?next=${encodeURIComponent(pathname + request.nextUrl.search)}`;
  return NextResponse.redirect(redirect);
}
