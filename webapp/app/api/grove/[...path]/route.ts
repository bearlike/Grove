import { NextRequest, NextResponse } from "next/server";
import { isAuthOk, resolveAuth } from "@/lib/auth/with-auth";
import { COOKIE_NAME, sharedCookieStore } from "@/lib/auth/cookie-store";

const DAEMON = process.env.GROVE_DAEMON_URL ?? "http://127.0.0.1:7421";

async function proxy(req: NextRequest, path: string[]) {
  const auth = await resolveAuth(req);
  if (!isAuthOk(auth)) return auth;

  const search = req.nextUrl.search;
  const upstream = `${DAEMON}/${path.join("/")}${search}`;
  const headers: Record<string, string> = {
    accept: "application/json",
    authorization: `Bearer ${auth.daemonToken}`,
  };
  const init: RequestInit = {
    method: req.method,
    headers,
    cache: "no-store",
  };
  if (req.method !== "GET" && req.method !== "HEAD") {
    init.body = await req.text();
    headers["content-type"] = req.headers.get("content-type") ?? "application/json";
  }
  try {
    const res = await fetch(upstream, init);
    const body = await res.text();
    // If the daemon revoked the session out from under us, clear our cookie
    // so the browser falls back to /login on its next page nav.
    const out = new NextResponse(body, {
      status: res.status,
      headers: {
        "content-type": res.headers.get("content-type") ?? "application/json",
      },
    });
    if (res.status === 401) {
      // Daemon says the token is bad — drop the local cookie too.
      const cookieId = req.cookies.get(COOKIE_NAME)?.value;
      if (cookieId) {
        await sharedCookieStore().revoke(cookieId);
      }
      out.cookies.delete(COOKIE_NAME);
    }
    return out;
  } catch (err) {
    return NextResponse.json(
      { detail: { error: "daemon_unreachable", message: String(err) } },
      { status: 502 },
    );
  }
}

export async function GET(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  return proxy(req, path);
}

export async function POST(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  return proxy(req, path);
}

export async function DELETE(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  return proxy(req, path);
}

export async function PATCH(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  return proxy(req, path);
}
