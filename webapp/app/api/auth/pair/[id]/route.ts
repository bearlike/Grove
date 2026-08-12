import { NextRequest, NextResponse } from "next/server";
import { COOKIE_NAME, isSecureRequest, sharedCookieStore } from "@/lib/auth/cookie-store";

const daemonUrl = process.env.GROVE_DAEMON_URL ?? "http://127.0.0.1:7421";
type PairResponse = { challenge_id?: string; state?: string; token?: string | null; expires_at?: string | null };
type SessionResponse = { session_id: string; label: string };

export async function GET(request: NextRequest, context: { params: Promise<{ id: string }> }): Promise<Response> {
  const { id } = await context.params;
  let upstream: Response;
  try {
    upstream = await fetch(`${daemonUrl}/auth/pair/${id}`, { headers: { accept: "application/json" }, cache: "no-store" });
  } catch (error: unknown) {
    return daemonUnreachable(error);
  }
  const raw = await upstream.text();
  if (!upstream.ok) return new NextResponse(raw, { status: upstream.status, headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" } });

  let pairing: PairResponse;
  try { pairing = JSON.parse(raw) as PairResponse; } catch { return NextResponse.json({ detail: { error: "bad_upstream", message: "non-JSON response from daemon" } }, { status: 502 }); }
  if (pairing.state !== "consumed" || !pairing.token) return NextResponse.json({ challenge_id: pairing.challenge_id, state: pairing.state });

  const expiresAt = pairing.expires_at ?? new Date(Date.now() + 30 * 24 * 60 * 60 * 1_000).toISOString();
  const session = await daemonSession(pairing.token);
  const cookieId = await sharedCookieStore().issue({ daemonToken: pairing.token, sessionId: session?.session_id ?? "", label: session?.label ?? "", expiresAt });
  const response = NextResponse.json({ challenge_id: pairing.challenge_id, state: pairing.state });
  response.cookies.set({
    name: COOKIE_NAME,
    value: cookieId,
    httpOnly: true,
    sameSite: "lax",
    secure: isSecureRequest(request.headers.get("x-forwarded-proto"), request.nextUrl.protocol),
    path: "/",
    maxAge: Math.max(60, Math.floor((new Date(expiresAt).getTime() - Date.now()) / 1_000)),
  });
  return response;
}

async function daemonSession(token: string): Promise<SessionResponse | null> {
  try {
    const response = await fetch(`${daemonUrl}/auth/sessions/me`, { headers: { accept: "application/json", authorization: `Bearer ${token}` }, cache: "no-store" });
    return response.ok ? (await response.json()) as SessionResponse : null;
  } catch { return null; }
}

function daemonUnreachable(error: unknown): NextResponse {
  return NextResponse.json({ detail: { error: "daemon_unreachable", message: String(error) } }, { status: 502 });
}
