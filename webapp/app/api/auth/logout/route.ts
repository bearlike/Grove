import { NextRequest, NextResponse } from "next/server";
import { COOKIE_NAME, sharedCookieStore } from "@/lib/auth/cookie-store";

const daemonUrl = process.env.GROVE_DAEMON_URL ?? "http://127.0.0.1:7421";

export async function POST(request: NextRequest): Promise<NextResponse> {
  const cookieId = request.cookies.get(COOKIE_NAME)?.value;
  if (cookieId) {
    const entry = await sharedCookieStore().lookup(cookieId);
    if (entry?.sessionId) {
      try {
        await fetch(`${daemonUrl}/auth/sessions/${entry.sessionId}`, { method: "DELETE", headers: { authorization: `Bearer ${entry.daemonToken}` } });
      } catch { /* Local revocation still logs the browser out when the daemon is unavailable. */ }
    }
    await sharedCookieStore().revoke(cookieId);
  }
  const response = NextResponse.json({ ok: true });
  response.cookies.delete(COOKIE_NAME);
  return response;
}
