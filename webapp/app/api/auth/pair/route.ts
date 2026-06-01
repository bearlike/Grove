/**
 * `POST /api/auth/pair` — start a pairing session.
 *
 * Unauthenticated by design — this is the bootstrap endpoint. Forwards
 * a `{label}` body to the daemon's `POST /auth/pair`; the response
 * carries a challenge id + code the user reads off the TUI / `grove auth pending`.
 */
import { NextRequest, NextResponse } from "next/server";

const DAEMON = process.env.GROVE_DAEMON_URL ?? "http://127.0.0.1:7421";

export async function POST(req: NextRequest) {
  const body = await req.text();
  try {
    const upstream = await fetch(`${DAEMON}/auth/pair`, {
      method: "POST",
      headers: {
        accept: "application/json",
        "content-type": req.headers.get("content-type") ?? "application/json",
      },
      body,
    });
    const text = await upstream.text();
    return new NextResponse(text, {
      status: upstream.status,
      headers: {
        "content-type": upstream.headers.get("content-type") ?? "application/json",
      },
    });
  } catch (err) {
    return NextResponse.json(
      { detail: { error: "daemon_unreachable", message: String(err) } },
      { status: 502 },
    );
  }
}
