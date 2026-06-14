import { NextRequest, NextResponse } from "next/server";
import { isAuthOk, resolveAuth } from "@/lib/auth/with-auth";
import { COOKIE_NAME, sharedCookieStore } from "@/lib/auth/cookie-store";

const DAEMON = process.env.GROVE_DAEMON_URL ?? "http://127.0.0.1:7421";

// SSE is a long-lived response that must not be buffered or statically
// optimized — force the Node runtime + dynamic rendering for the whole route.
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Which GET paths are SSE event-streams that must be piped, not buffered:
 * the cross-project activity stream (`/events`) and the focused live-pane
 * stream (`/workspaces/{id}/pane/stream`, #19). Both push `text/event-stream`,
 * so both ride `proxyStream`; everything else buffers through `proxy`.
 */
function isEventStreamPath(path: string[]): boolean {
  if (path.length === 1 && path[0] === "events") return true;
  return (
    path.length === 4 &&
    path[0] === "workspaces" &&
    path[2] === "pane" &&
    path[3] === "stream"
  );
}

/**
 * Stream an SSE endpoint straight through from the daemon (SSE pass-through).
 *
 * The browser's cookie-auth `EventSource` hits this BFF; we inject the daemon
 * bearer server-side (the token never reaches the browser) and pipe
 * `upstream.body` untouched. `req.signal` is forwarded so a browser disconnect
 * aborts the upstream fetch (no `ResponseAborted` leak), and the proxy headers
 * (`no-transform`, `X-Accel-Buffering: no`) stop any intermediary from buffering
 * the event stream.
 */
async function proxyStream(req: NextRequest, path: string[]): Promise<Response> {
  const auth = await resolveAuth(req);
  if (!isAuthOk(auth)) return auth;

  const upstream = `${DAEMON}/${path.join("/")}${req.nextUrl.search}`;
  try {
    const res = await fetch(upstream, {
      method: "GET",
      headers: {
        accept: "text/event-stream",
        authorization: `Bearer ${auth.daemonToken}`,
        // Forward the browser's resume cursor so the daemon can replay missed
        // deltas instead of re-sending a full snapshot.
        ...(req.headers.get("last-event-id")
          ? { "last-event-id": req.headers.get("last-event-id") as string }
          : {}),
      },
      cache: "no-store",
      signal: req.signal,
    });
    if (!res.ok || !res.body) {
      return NextResponse.json(
        { detail: { error: "daemon_error", message: `stream upstream ${res.status}` } },
        { status: res.status || 502 },
      );
    }
    return new Response(res.body, {
      status: 200,
      headers: {
        "content-type": "text/event-stream; charset=utf-8",
        "cache-control": "no-cache, no-transform",
        "x-accel-buffering": "no",
        connection: "keep-alive",
      },
    });
  } catch (err) {
    return NextResponse.json(
      { detail: { error: "daemon_unreachable", message: String(err) } },
      { status: 502 },
    );
  }
}

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
    // A bodyless status (the steering endpoints reply 204) must re-wrap with
    // null — `new Response("", { status: 204 })` throws, and the catch below
    // would mislabel the daemon's success as daemon_unreachable.
    const bodyAllowed = res.status !== 204 && res.status !== 205 && res.status !== 304;
    // If the daemon revoked the session out from under us, clear our cookie
    // so the browser falls back to /login on its next page nav.
    const out = new NextResponse(bodyAllowed ? body : null, {
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
  // SSE event-streams (activity + focused pane) are piped, not buffered.
  if (isEventStreamPath(path)) {
    return proxyStream(req, path);
  }
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
