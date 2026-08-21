import { NextRequest, NextResponse } from "next/server";

import { SHARE_PASSCODE_HEADER } from "@/lib/grove/api/share-passcode";

const daemonUrl = process.env.GROVE_DAEMON_URL ?? "http://127.0.0.1:7421";

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
      //
      // The share passcode is forwarded VERBATIM and is the one header this
      // road relays. It is the reader's own secret, not the host's — this
      // proxy never holds, stores or substitutes it, so a caller with no
      // passcode simply gets the daemon's 401 rather than a silently
      // privileged read. Forwarded ONLY when present, so a project with no
      // passcode is byte-identical to before.
      headers: {
        accept: "application/json",
        ...passcodeHeader(request),
      },
      cache: "no-store",
    });
    return new NextResponse(
      [204, 205, 304].includes(response.status) ? null : await response.text(),
      {
        status: response.status,
        headers: {
          "content-type":
            response.headers.get("content-type") ?? "application/json",
        },
      },
    );
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
  return (
    segments.length === 0 ||
    (segments.length === 1 &&
      (segments[0] === "turns" || segments[0] === "diff"))
  );
}

/** The share passcode as the daemon expects it, or nothing at all. */
function passcodeHeader(request: NextRequest): Record<string, string> {
  const passcode = request.headers.get(SHARE_PASSCODE_HEADER);
  return passcode ? { [SHARE_PASSCODE_HEADER]: passcode } : {};
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
