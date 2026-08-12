import { afterEach, describe, expect, it, vi } from "vitest";

import { GET } from "@/app/api/version/route";

/**
 * The login screen's one factual claim, and the rule that keeps it honest.
 *
 * The version is shown so a user can see what they are pairing WITH, which makes
 * a WRONG number strictly worse than no number — it is confidently incorrect and
 * nothing on screen says so. Everything below pins the same guarantee from a
 * different angle: this route only ever answers 200 when it is repeating a
 * string the daemon actually sent.
 *
 * The daemon's side is verified separately and by hand, because a unit test
 * cannot: `GET /healthz` answers 200 unauthenticated while `/whoami` answers
 * 401, and `HealthView`'s contract calls `version` public-safe.
 */
const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

/** Stub the one upstream call, so every branch is reachable with no daemon. */
function daemonAnswers(body: unknown, status = 200): void {
  globalThis.fetch = vi.fn(
    async () =>
      new Response(JSON.stringify(body), {
        status,
        headers: { "content-type": "application/json" },
      }),
  ) as unknown as typeof fetch;
}

describe("the pre-auth version route", () => {
  it("repeats the daemon's version verbatim", async () => {
    daemonAnswers({ status: "ok", version: "0.0.6" });
    const response = await GET();
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ version: "0.0.6" });
  });

  // The reason this route exists rather than a build constant: a version from
  // the wrong process is the failure being prevented, so an unreachable daemon
  // must produce NO version rather than a fallback one.
  it("reports unavailable when the daemon cannot be reached", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new TypeError("fetch failed");
    }) as unknown as typeof fetch;
    const response = await GET();
    expect(response.status).toBe(503);
    expect(await response.json()).not.toHaveProperty("version");
  });

  it("reports unavailable when the daemon answers non-2xx", async () => {
    daemonAnswers({ detail: "boom" }, 502);
    expect((await GET()).status).toBe(503);
  });

  // Narrowing at the boundary, not decoration: an unnarrowed value here prints
  // `[object Object]` where a version belongs, on the first screen a user sees.
  it("refuses a payload whose version is not a string", async () => {
    for (const version of [undefined, null, 6, { major: 0 }, ["0.0.6"]]) {
      daemonAnswers({ status: "ok", version });
      const response = await GET();
      expect(response.status, `version=${JSON.stringify(version)}`).toBe(503);
    }
  });

  it("never leaks anything but the version out of healthz", async () => {
    daemonAnswers({ status: "ok", version: "0.0.6", hostname: "a-host", user: "someone" });
    const body = await (await GET()).json();
    expect(Object.keys(body)).toEqual(["version"]);
  });
});
