/**
 * `POST /api/auth/logout` — drop the cookie + revoke the daemon session.
 *
 * Idempotent: a second call after the cookie is gone returns 200 with no
 * action. Always sets the deleted cookie on the response so a client
 * with a stale cookie also gets cleared.
 */
import { NextRequest, NextResponse } from "next/server";
import { COOKIE_NAME, sharedCookieStore } from "@/lib/auth/cookie-store";

const DAEMON = process.env.GROVE_DAEMON_URL ?? "http://127.0.0.1:7421";

export async function POST(req: NextRequest) {
  const cookieId = req.cookies.get(COOKIE_NAME)?.value;
  if (cookieId) {
    const entry = await sharedCookieStore().lookup(cookieId);
    if (entry?.sessionId) {
      // Best-effort daemon revoke — if the daemon is down, we still clear
      // the local cookie so the user is locally logged out.
      try {
        await fetch(`${DAEMON}/auth/sessions/${entry.sessionId}`, {
          method: "DELETE",
          headers: { authorization: `Bearer ${entry.daemonToken}` },
        });
      } catch {
        // Swallow — best-effort.
      }
    }
    await sharedCookieStore().revoke(cookieId);
  }
  const out = NextResponse.json({ ok: true });
  out.cookies.delete(COOKIE_NAME);
  return out;
}
