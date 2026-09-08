import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

import { GET } from "@/app/api/public/[token]/[[...path]]/route";
import {
  publicStreamAction,
  publicStreamReconnectDelay,
} from "@/lib/grove/hooks/public";

const originalFetch = globalThis.fetch;

function upstream(body = "{}", status = 200, contentType = "application/json"): void {
  globalThis.fetch = vi.fn(
    async () =>
      new Response(body, {
        status,
        headers: { "content-type": contentType },
      }),
  ) as unknown as typeof fetch;
}

function context(token = "shared-link") {
  return { params: Promise.resolve({ token, path: ["events"] }) };
}

afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("public stream invalidation", () => {
  it.each(["snapshot", "changed"])("refreshes public reads for %s", (kind) => {
    expect(publicStreamAction(kind)).toBe("invalidate");
  });

  it("does nothing for a heartbeat or an unknown event", () => {
    expect(publicStreamAction("heartbeat")).toBe("ignore");
    expect(publicStreamAction("session_activity")).toBe("ignore");
  });

  it("caps reconnect delay instead of growing without bound", () => {
    expect(publicStreamReconnectDelay(1)).toBe(1_000);
    expect(publicStreamReconnectDelay(2)).toBe(2_000);
    expect(publicStreamReconnectDelay(99)).toBe(10_000);
  });
});

describe("public stream BFF", () => {
  it("forwards the path-scoped passcode cookie as the daemon header", async () => {
    upstream("event: snapshot\ndata: {}\n\n", 200, "text/event-stream");
    const request = new NextRequest("http://localhost/api/public/shared-link/events", {
      headers: { cookie: "grove_share_passcode=reader-secret" },
    });

    const response = await GET(request, context());

    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toBe("text/event-stream");
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "http://127.0.0.1:7421/public/shared-link/events",
      expect.objectContaining({
        headers: expect.objectContaining({
          "x-grove-share-passcode": "reader-secret",
        }),
      }),
    );
  });

  it("turns an accepted overview header into the EventSource cookie", async () => {
    upstream();
    const request = new NextRequest("http://localhost/api/public/shared-link", {
      headers: { "x-grove-share-passcode": "reader-secret" },
    });

    const response = await GET(request, {
      params: Promise.resolve({ token: "shared-link" }),
    });

    expect(response.headers.get("set-cookie")).toContain("grove_share_passcode=reader-secret");
    expect(response.headers.get("set-cookie")).toContain("Path=/api/public/shared-link/events");
    expect(response.headers.get("set-cookie")).toContain("HttpOnly");
  });

  it("never gives a regular public read a passcode cookie", async () => {
    upstream();
    const request = new NextRequest("http://localhost/api/public/shared-link/events", {
      headers: { "x-grove-share-passcode": "reader-secret" },
    });

    const response = await GET(request, context());

    expect(response.headers.get("set-cookie")).toBeNull();
  });
});
