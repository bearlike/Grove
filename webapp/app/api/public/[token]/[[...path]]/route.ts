import { NextRequest, NextResponse } from "next/server";

import { SHARE_PASSCODE_HEADER } from "@/lib/grove/api/share-passcode";

const daemonUrl = process.env.GROVE_DAEMON_URL ?? "http://127.0.0.1:7421";
const SHARE_PASSCODE_COOKIE = "grove_share_passcode";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type Context = { params: Promise<{ token: string; path?: string[] }> };

/**
 * The deliberately narrow, bearer-free road from a public page to its share.
 *
 * WHY THE DOUBLE-BRACKET CATCH-ALL. The overview is the zero-segment case after
 * its token. A single-bracket catch-all only matches one or more segments, so
 * it silently 404s `/api/public/<token>` before this handler can authorize its
 * deliberately empty subpath.
 *
 * WHY IT IS ITS OWN ROUTE RATHER THAN AN EXEMPTION IN THE CATCH-ALL PROXY.
 * `app/api/grove/[...path]` has one security invariant: a session authorizes
 * every daemon request it relays. Keeping this road in that file would turn
 * every later catch-all edit into a public-data review. This route can only
 * ever assemble `/public/*`, and the fixed subpath allowlist below means it
 * cannot widen beyond the daemon's explicitly public surface.
 */
export async function GET(
  request: NextRequest,
  context: Context,
): Promise<Response> {
  const { token, path } = await context.params;
  if (!isPublicPath(path)) return unknownPublicPath(path);

  try {
    const response = await fetch(upstreamUrl(token, path ?? [], request), {
      // The token is the daemon path capability. There is intentionally no
      // `Authorization` header on this road: attaching a browser session would
      // make a share request an authenticated request by accident.
      headers: {
        accept: acceptsEventStream(path) ? "text/event-stream" : "application/json",
        ...passcodeHeader(request),
      },
      cache: "no-store",
      signal: request.signal,
    });
    return upstreamResponse(response, request, token, path);
  } catch (error: unknown) {
    return daemonUnreachable(error);
  }
}

/**
 * The only daemon subpaths a capability link may reach.
 *
 * This is deliberately a structural allowlist, rather than forwarding whatever
 * the catch-all segment happened to contain: no unreviewed path spelling can
 * turn a public link into a route to workspace, host, or auth data. `GET` is
 * deliberately the only exported handler above, so writes receive Next's 405
 * without any code path that could forward them.
 */
function isPublicPath(path: string[] | undefined): boolean {
  const segments = path ?? [];
  if (segments.length === 0) return true;
  if (segments.length === 1)
    return segments[0] === "turns" || segments[0] === "diff" || segments[0] === "events";
  // `tools/<tool_use_id>` is the only TWO-segment shape, and it is still
  // structural: the id is a path parameter the daemon resolves against the
  // token's own session, never a subpath a caller could extend. A wildcard tail
  // here would be the unreviewed spelling this allowlist exists to refuse.
  return segments.length === 2 && segments[0] === "tools" && segments[1].length > 0;
}

function acceptsEventStream(path: string[] | undefined): boolean {
  return path?.length === 1 && path[0] === "events";
}

/**
 * The request header wins because an ordinary public read is the only place a
 * browser can introduce a passcode. The BFF then scopes it to this token's
 * EventSource endpoint, whose API has no header parameter.
 */
function passcodeHeader(request: NextRequest): Record<string, string> {
  const supplied = request.headers.get(SHARE_PASSCODE_HEADER);
  if (supplied) return { [SHARE_PASSCODE_HEADER]: supplied };

  const stored = request.cookies.get(SHARE_PASSCODE_COOKIE)?.value;
  const passcode = stored ? decodePasscode(stored) : null;
  return passcode ? { [SHARE_PASSCODE_HEADER]: passcode } : {};
}

function decodePasscode(value: string): string | null {
  try {
    return decodeURIComponent(value);
  } catch {
    // A malformed local cookie is equivalent to no usable credential. Sending
    // it verbatim would convert a client-side corruption into an auth bypass
    // candidate at a boundary that must only relay a reader-supplied passcode.
    return null;
  }
}

function upstreamResponse(
  response: Response,
  request: NextRequest,
  token: string,
  path: string[] | undefined,
): NextResponse {
  const result = new NextResponse(response.body, {
    status: response.status,
    headers: {
      "content-type": response.headers.get("content-type") ?? "application/json",
      ...(acceptsEventStream(path)
        ? {
            "cache-control": response.headers.get("cache-control") ?? "no-cache, no-transform",
            "x-accel-buffering": response.headers.get("x-accel-buffering") ?? "no",
          }
        : {}),
    },
  });

  const supplied = request.headers.get(SHARE_PASSCODE_HEADER);
  if (response.ok && supplied && !acceptsEventStream(path)) {
    setEventStreamPasscode(result, request, token, supplied);
  }
  return result;
}

function setEventStreamPasscode(
  response: NextResponse,
  request: NextRequest,
  token: string,
  passcode: string,
): void {
  response.cookies.set({
    name: SHARE_PASSCODE_COOKIE,
    value: encodeURIComponent(passcode),
    httpOnly: true,
    sameSite: "lax",
    secure: request.nextUrl.protocol === "https:",
    path: `/api/public/${encodeURIComponent(token)}/events`,
  });
}

function upstreamUrl(
  token: string,
  path: string[],
  request: NextRequest,
): string {
  const suffix = path.length === 0 ? "" : `/${path[0]}`;
  // Preserve the daemon's cursor and per-file-diff query contracts exactly;
  // parsing and rebuilding them here risks quietly changing their semantics.
  return `${daemonUrl}/public/${encodeURIComponent(token)}${suffix}${request.nextUrl.search}`;
}

function unknownPublicPath(path: string[] | undefined): NextResponse {
  return NextResponse.json(
    {
      detail: {
        error: "public_path_not_found",
        message: `Public resource not found: ${(path ?? []).join("/")}`,
      },
    },
    { status: 404 },
  );
}

function daemonUnreachable(error: unknown): NextResponse {
  return NextResponse.json(
    { detail: { error: "daemon_unreachable", message: String(error) } },
    { status: 502 },
  );
}
