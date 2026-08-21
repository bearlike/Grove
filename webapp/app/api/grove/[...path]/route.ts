import { NextRequest, NextResponse } from "next/server";
import { COOKIE_NAME, sharedCookieStore } from "@/lib/auth/cookie-store";
import { isAuthOk, resolveAuth } from "@/lib/auth/with-auth";

const daemonUrl = process.env.GROVE_DAEMON_URL ?? "http://127.0.0.1:7421";
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
type Context = { params: Promise<{ path: string[] }> };

export async function GET(request: NextRequest, context: Context): Promise<Response> {
  const { path } = await context.params;
  return isEventStream(path) ? proxyStream(request, path) : proxy(request, path);
}
// EVERY method the daemon serves needs an export here, and a missing one fails
// in a way nothing in this file can see: Next matches the route, finds no
// handler for the verb, and answers 405 before any code below runs. That is
// what happened to `PUT /share-policy` — the daemon route, the client method
// and the typed schema were all correct and the browser still got a 405, so the
// bug reads as a daemon problem while living entirely in this list.
//
// `tests/daemon/test_bff_method_parity.py` now pins the set against the app's
// real route table, because this file cannot check itself.
export async function POST(request: NextRequest, context: Context): Promise<Response> { return proxyParams(request, context); }
export async function PUT(request: NextRequest, context: Context): Promise<Response> { return proxyParams(request, context); }
export async function DELETE(request: NextRequest, context: Context): Promise<Response> { return proxyParams(request, context); }
export async function PATCH(request: NextRequest, context: Context): Promise<Response> { return proxyParams(request, context); }

async function proxyParams(request: NextRequest, context: Context): Promise<Response> {
  return proxy(request, (await context.params).path);
}

function isEventStream(path: string[]): boolean {
  return (path.length === 1 && path[0] === "events") || (path.length === 4 && path[0] === "workspaces" && path[2] === "pane" && path[3] === "stream");
}

async function proxyStream(request: NextRequest, path: string[]): Promise<Response> {
  const auth = await resolveAuth(request);
  if (!isAuthOk(auth)) return auth;
  try {
    const response = await fetch(upstreamUrl(path, request), {
      headers: { accept: "text/event-stream", authorization: `Bearer ${auth.daemonToken}`, ...(request.headers.get("last-event-id") ? { "last-event-id": request.headers.get("last-event-id")! } : {}) },
      cache: "no-store",
      signal: request.signal,
    });
    if (!response.ok || !response.body) return daemonError(response.status, `stream upstream ${response.status}`);
    return new Response(response.body, { headers: { "content-type": "text/event-stream; charset=utf-8", "cache-control": "no-cache, no-transform", "x-accel-buffering": "no", connection: "keep-alive" } });
  } catch (error: unknown) { return daemonUnreachable(error); }
}

async function proxy(request: NextRequest, path: string[]): Promise<Response> {
  const auth = await resolveAuth(request);
  if (!isAuthOk(auth)) return auth;
  const headers: Record<string, string> = { accept: "application/json", authorization: `Bearer ${auth.daemonToken}` };
  const init: RequestInit = { method: request.method, headers, cache: "no-store" };
  if (request.method !== "GET" && request.method !== "HEAD") {
    init.body = await request.text();
    headers["content-type"] = request.headers.get("content-type") ?? "application/json";
  }
  try {
    const response = await fetch(upstreamUrl(path, request), init);
    const output = new NextResponse([204, 205, 304].includes(response.status) ? null : await response.text(), { status: response.status, headers: { "content-type": response.headers.get("content-type") ?? "application/json" } });
    if (response.status === 401) await clearInvalidCookie(request, output);
    return output;
  } catch (error: unknown) { return daemonUnreachable(error); }
}

function upstreamUrl(path: string[], request: NextRequest): string { return `${daemonUrl}/${path.join("/")}${request.nextUrl.search}`; }
function daemonError(status: number, message: string): NextResponse { return NextResponse.json({ detail: { error: "daemon_error", message } }, { status: status || 502 }); }
function daemonUnreachable(error: unknown): NextResponse { return NextResponse.json({ detail: { error: "daemon_unreachable", message: String(error) } }, { status: 502 }); }

async function clearInvalidCookie(request: NextRequest, response: NextResponse): Promise<void> {
  const cookieId = request.cookies.get(COOKIE_NAME)?.value;
  if (cookieId) await sharedCookieStore().revoke(cookieId);
  response.cookies.delete(COOKIE_NAME);
}
