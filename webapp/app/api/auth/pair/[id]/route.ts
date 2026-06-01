/**
 * `GET /api/auth/pair/[id]` — poll a pending pairing.
 *
 * On the first response with `state == "consumed"` and a `token`, this
 * route writes a server-side `CookieStore` entry, sets the HttpOnly
 * `grove_session` cookie on the browser, and STRIPS the token from the
 * response body before returning. The browser only ever sees `state` —
 * the daemon token never reaches the client side.
 */
import { NextRequest, NextResponse } from "next/server";
import { COOKIE_NAME, sharedCookieStore } from "@/lib/auth/cookie-store";

const DAEMON = process.env.GROVE_DAEMON_URL ?? "http://127.0.0.1:7421";

export async function GET(
  req: NextRequest,
  ctx: { params: Promise<{ id: string }> },
) {
  const { id } = await ctx.params;
  let upstreamResp: Response;
  try {
    upstreamResp = await fetch(`${DAEMON}/auth/pair/${id}`, {
      method: "GET",
      headers: { accept: "application/json" },
      cache: "no-store",
    });
  } catch (err) {
    return NextResponse.json(
      { detail: { error: "daemon_unreachable", message: String(err) } },
      { status: 502 },
    );
  }
  const raw = await upstreamResp.text();
  // Pass through non-200 responses unchanged so the client sees the
  // daemon's error envelope.
  if (!upstreamResp.ok) {
    return new NextResponse(raw, {
      status: upstreamResp.status,
      headers: {
        "content-type":
          upstreamResp.headers.get("content-type") ?? "application/json",
      },
    });
  }
  let body: {
    challenge_id?: string;
    state?: string;
    token?: string | null;
    expires_at?: string | null;
  };
  try {
    body = JSON.parse(raw);
  } catch {
    return NextResponse.json(
      { detail: { error: "bad_upstream", message: "non-JSON response from daemon" } },
      { status: 502 },
    );
  }

  // Pending / denied / expired: pass through with no token field.
  if (body.state !== "consumed" || !body.token) {
    return NextResponse.json(
      {
        challenge_id: body.challenge_id,
        state: body.state,
      },
      { status: 200 },
    );
  }

  // Consume step: extract the daemon token, look up its session id, persist
  // the cookie mapping, set the HttpOnly cookie, return state-only body.
  const daemonToken = body.token;
  const expiresAt = body.expires_at ?? new Date(Date.now() + 30 * 24 * 3600_000).toISOString();
  let sessionId = "";
  let label = "";
  try {
    const meResp = await fetch(`${DAEMON}/auth/sessions/me`, {
      headers: {
        accept: "application/json",
        authorization: `Bearer ${daemonToken}`,
      },
      cache: "no-store",
    });
    if (meResp.ok) {
      const me = (await meResp.json()) as { session_id: string; label: string };
      sessionId = me.session_id;
      label = me.label;
    }
  } catch {
    // Best-effort: if /sessions/me fails, we still set the cookie with
    // empty session_id — logout will skip the daemon revoke step.
  }

  const cookieId = await sharedCookieStore().issue({
    daemonToken,
    sessionId,
    label,
    expiresAt,
  });

  const out = NextResponse.json(
    {
      challenge_id: body.challenge_id,
      state: body.state,
    },
    { status: 200 },
  );
  // Cookie attributes: HttpOnly (no JS access), SameSite=Strict (no CSRF
  // surface), Secure when the request was over HTTPS, Path=/ (covers all
  // routes), Max-Age aligned with the session expiry.
  const maxAgeSeconds = Math.max(
    60,
    Math.floor((new Date(expiresAt).getTime() - Date.now()) / 1000),
  );
  out.cookies.set({
    name: COOKIE_NAME,
    value: cookieId,
    httpOnly: true,
    sameSite: "strict",
    secure: req.nextUrl.protocol === "https:",
    path: "/",
    maxAge: maxAgeSeconds,
  });
  return out;
}
