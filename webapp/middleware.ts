/**
 * Auth gate middleware.
 *
 * Pages without a `grove_session` cookie redirect to `/login?next=<path>`.
 * Allow-listed: `/login` (the gate itself), `/api/auth/*` (pairing /
 * logout endpoints — `/api/auth/pair*` is unauthenticated by design,
 * `/api/auth/logout` clears its own cookie), Next.js internals, static
 * assets. Everything else falls through to the auth gate.
 *
 * Note: this middleware ONLY checks for cookie presence — the actual
 * validation against the server-side ``CookieStore`` happens inside the
 * `/api/grove/[...path]` proxy. A forged cookie value gets a 401 from
 * the proxy on its first request; the absence of a cookie short-circuits
 * here so the user sees /login immediately.
 */
import { NextRequest, NextResponse } from "next/server";

export const config = {
  // Skip Next.js internals + static assets + manifest + favicon. Run the
  // matcher on every other path so nav-style page loads pass through here
  // and the auth check fires before any page renders.
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|manifest.webmanifest|.*\\..*).*)",
  ],
};

const COOKIE_NAME = "grove_session";

const PUBLIC_PATHS = new Set<string>(["/login"]);

function isPublic(pathname: string): boolean {
  if (PUBLIC_PATHS.has(pathname)) return true;
  if (pathname.startsWith("/api/auth/")) return true;
  // Next.js internal endpoints (dev-mode HMR etc.).
  if (pathname.startsWith("/_next/")) return true;
  return false;
}

export function middleware(req: NextRequest) {
  const pathname = req.nextUrl.pathname;
  if (isPublic(pathname)) {
    return NextResponse.next();
  }
  const hasCookie = Boolean(req.cookies.get(COOKIE_NAME)?.value);
  if (hasCookie) {
    return NextResponse.next();
  }
  // Browser hits a protected page → bounce to /login carrying the original
  // path so we can return them to it after pair completes.
  const url = req.nextUrl.clone();
  url.pathname = "/login";
  url.search = `?next=${encodeURIComponent(pathname + req.nextUrl.search)}`;
  return NextResponse.redirect(url);
}
