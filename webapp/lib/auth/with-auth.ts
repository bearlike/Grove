import { NextRequest, NextResponse } from "next/server";
import { COOKIE_NAME, sharedCookieStore } from "./cookie-store";

export type AuthContext = { daemonToken: string; sessionId: string; label: string };

export async function resolveAuth(request: NextRequest): Promise<AuthContext | NextResponse> {
  const cookieId = request.cookies.get(COOKIE_NAME)?.value;
  if (!cookieId) return unauthorized("auth_missing", "no session cookie");

  const entry = await sharedCookieStore().lookup(cookieId);
  if (!entry) {
    const response = unauthorized("auth_invalid", "session expired or revoked");
    response.cookies.delete(COOKIE_NAME);
    return response;
  }
  return { daemonToken: entry.daemonToken, sessionId: entry.sessionId, label: entry.label };
}

export function isAuthOk(value: AuthContext | NextResponse): value is AuthContext {
  return !(value instanceof NextResponse);
}

function unauthorized(error: string, message: string): NextResponse {
  return NextResponse.json({ detail: { error, message } }, { status: 401 });
}
