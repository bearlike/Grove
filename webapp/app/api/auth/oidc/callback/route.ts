import { NextRequest, NextResponse } from "next/server";
import { COOKIE_NAME, isSecureRequest, sharedCookieStore } from "@/lib/auth/cookie-store";
import { OIDC_COOKIE, OidcClient, callbackUrl, oidcConfig, publicOrigin, type PendingLogin } from "@/lib/auth/oidc";

const daemonUrl = process.env.GROVE_DAEMON_URL ?? "http://127.0.0.1:7421";
type SignIn = { token: string; expires_at: string; subject: string };
type SessionResponse = { session_id: string; label: string };

/**
 * The IdP sends the browser back here. The ID token is exchanged at the daemon,
 * which verifies it itself, for an ordinary Grove bearer held exactly as a paired
 * browser's is: in the server-side cookie store, behind an opaque cookie.
 */
export async function GET(request: NextRequest): Promise<NextResponse> {
  const config = oidcConfig();
  const pending = readPending(request.cookies.get(OIDC_COOKIE)?.value);
  if (!config) return finish(request, "/login", "oidc_not_configured");
  if (!pending) return finish(request, "/login", "oidc_expired");

  let idToken: string;
  try {
    const client = await OidcClient.discover(config);
    // `request.nextUrl` is Next's own URL-like class, not a `URL`, and the
    // library refuses anything but a real `URL`/`URLSearchParams` before it
    // reads one parameter: every live login failed here as "refused" while
    // the bogus-code replays failed at the same line and read identically.
    idToken = await client.complete(
      new URL(request.url),
      callbackUrl(publicOrigin(request.headers, request.nextUrl.origin)),
      pending,
    );
  } catch (error) {
    // The browser only ever learns "refused" (a crafted error must not put the
    // IdP's words on the page), so the one place the real reason survives is
    // this server's log. Without it a refused login is indistinguishable from
    // a lost cookie, a wrong secret or a rejected token (2026-10-22).
    console.warn("oidc callback refused:", describeRefusal(error));
    return finish(request, "/login", "oidc_refused");
  }

  let signIn: SignIn;
  try {
    const exchanged = await fetch(`${daemonUrl}/auth/oidc/session`, {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify({ id_token: idToken, label: "browser (oidc)" }),
      cache: "no-store",
    });
    if (!exchanged.ok) return finish(request, "/login", exchanged.status === 404 ? "oidc_not_configured" : "oidc_refused");
    signIn = (await exchanged.json()) as SignIn;
  } catch {
    return finish(request, "/login", "daemon_unreachable");
  }

  const session = await daemonSession(signIn.token);
  const cookieId = await sharedCookieStore().issue({
    daemonToken: signIn.token,
    sessionId: session?.session_id ?? "",
    label: session?.label ?? "browser (oidc)",
    expiresAt: signIn.expires_at,
  });
  const response = finish(request, pending.next);
  response.cookies.set({
    name: COOKIE_NAME,
    value: cookieId,
    httpOnly: true,
    sameSite: "lax",
    secure: isSecureRequest(request.headers.get("x-forwarded-proto"), request.nextUrl.protocol),
    path: "/",
    maxAge: Math.max(60, Math.floor((new Date(signIn.expires_at).getTime() - Date.now()) / 1_000)),
  });
  return response;
}

/** Redirect onward and always drop the attempt cookie: it is single use, success or not. */
function finish(request: NextRequest, path: string, error?: string): NextResponse {
  const url = new URL(path, publicOrigin(request.headers, request.nextUrl.origin));
  if (error) url.searchParams.set("error", error);
  const response = NextResponse.redirect(url);
  response.cookies.set({ name: OIDC_COOKIE, value: "", path: "/api/auth/oidc", maxAge: 0 });
  return response;
}

/** The library's error code and the IdP's own error words, never a token or a code. */
function describeRefusal(error: unknown): string {
  if (!(error instanceof Error)) return String(error);
  const detail = error as Error & { code?: string; error?: string; error_description?: string; status?: number };
  return [
    detail.code ?? error.name,
    detail.status !== undefined ? `http ${detail.status}` : null,
    detail.error,
    detail.error_description,
    error.message,
  ]
    .filter(Boolean)
    .join(" | ");
}

function readPending(raw: string | undefined): PendingLogin | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(Buffer.from(raw, "base64url").toString("utf8")) as Partial<PendingLogin>;
    return typeof value.verifier === "string" && typeof value.state === "string" && typeof value.nonce === "string" && typeof value.next === "string"
      ? (value as PendingLogin)
      : null;
  } catch {
    return null;
  }
}

async function daemonSession(token: string): Promise<SessionResponse | null> {
  try {
    const response = await fetch(`${daemonUrl}/auth/sessions/me`, { headers: { accept: "application/json", authorization: `Bearer ${token}` }, cache: "no-store" });
    return response.ok ? ((await response.json()) as SessionResponse) : null;
  } catch {
    return null;
  }
}
