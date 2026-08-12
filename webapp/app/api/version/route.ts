import { NextResponse } from "next/server";

const daemonUrl = process.env.GROVE_DAEMON_URL ?? "http://127.0.0.1:7421";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * The daemon's own version, readable before anyone has paired.
 *
 * WHY THIS EXISTS RATHER THAN A BUILD CONSTANT. The login screen states the
 * version so a user can see what they are pairing WITH, and a number baked into
 * the front end at build time answers a different question — it describes the
 * web bundle, which can be an entirely different release from the process on the
 * other end of the socket. A figure from the wrong process is worse than no
 * figure, because it is confidently wrong and nothing on screen says so.
 *
 * WHY IT IS ITS OWN ROUTE RATHER THAN AN EXEMPTION IN THE CATCH-ALL PROXY.
 * `app/api/grove/[...path]` holds one invariant — nothing reaches the daemon
 * without a session — and that invariant is worth more than the file it would
 * save. Punching a path-matched hole in the gate makes every future edit there a
 * security review. This route can only ever fetch one URL, so it cannot widen.
 *
 * WHY THE VERSION IS SAFE TO SHOW UNAUTHENTICATED, and it is not this file's
 * opinion: `GET /healthz` is the daemon's public liveness probe, and
 * `HealthView`'s own contract calls `version` "public-safe — already advertised
 * in `/openapi.json`'s `info.version`". Verified live against the running daemon:
 * `/healthz` answers 200 with no credentials while `/whoami` answers 401.
 * Everything that identifies WHO runs the daemon — hostname, user, uptime —
 * stays behind auth, and none of it is read here.
 */
export async function GET(): Promise<Response> {
  try {
    const response = await fetch(`${daemonUrl}/healthz`, {
      headers: { accept: "application/json" },
      cache: "no-store",
    });
    if (!response.ok) return unavailable(`healthz ${response.status}`);
    const health = (await response.json()) as { version?: unknown };
    // Narrow at the boundary rather than trusting the shape: this renders into
    // the login screen, and a non-string here would print `[object Object]`
    // where a version belongs.
    return typeof health.version === "string"
      ? NextResponse.json({ version: health.version })
      : unavailable("healthz carried no version");
  } catch (error: unknown) {
    return unavailable(String(error));
  }
}

/**
 * 503 rather than a null version in a 200.
 *
 * The caller renders nothing when it cannot learn the version, and the two
 * situations it must not conflate are "the daemon says it is 0.0.7" and "nobody
 * answered". A status code separates them without the client inspecting a body.
 */
function unavailable(message: string): NextResponse {
  return NextResponse.json(
    { detail: { error: "version_unavailable", message } },
    { status: 503 },
  );
}
