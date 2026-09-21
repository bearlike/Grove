import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

const { resolveAuth } = vi.hoisted(() => ({
  resolveAuth: vi.fn(async () => ({
    daemonToken: "daemon-token",
    sessionId: "session-id",
    label: "Browser",
  })),
}));

vi.mock("@/lib/auth/with-auth", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/auth/with-auth")>()),
  resolveAuth,
}));

import { GET } from "@/app/api/grove/[...path]/route";

const originalFetch = globalThis.fetch;

function context(path = ["workspaces", "workspace-id", "sessions", "session-id", "turns"]) {
  return { params: Promise.resolve({ path }) };
}

afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("authenticated Grove BFF", () => {
  it("streams a JSON response without consuming its body", async () => {
    const read = vi.fn();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('{"turns":['));
        controller.enqueue(new TextEncoder().encode(']}'));
        controller.close();
      },
    });
    globalThis.fetch = vi.fn(async () => new Response(stream, {
      headers: { "content-type": "application/json" },
    })) as unknown as typeof fetch;

    const response = await GET(
      new NextRequest("http://localhost/api/grove/workspaces/workspace-id/sessions/session-id/turns?last=40"),
      context(),
    );

    // A `text()` implementation would consume this body before the BFF returns.
    // The response remains reader-owned until the caller asks for it.
    const reader = response.body?.getReader();
    expect(reader).toBeDefined();
    const first = await reader?.read();
    read(first);
    expect(first?.done).toBe(false);
    expect(new TextDecoder().decode(first?.value)).toBe('{"turns":[');
    reader?.releaseLock();
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "http://127.0.0.1:7421/workspaces/workspace-id/sessions/session-id/turns?last=40",
      expect.objectContaining({
        headers: expect.objectContaining({
          "accept-encoding": "identity",
          accept: "application/json",
          authorization: "Bearer daemon-token",
        }),
      }),
    );
  });

  it("does not forward stale gzip metadata over Node-decoded bytes", async () => {
    const body = new TextEncoder().encode('{"turns":[]}');
    globalThis.fetch = vi.fn(async () => new Response(body, {
      headers: {
        "content-type": "application/json",
        // This is exactly what Node's fetch exposes after it transparently
        // decodes an upstream gzip body: stale compression metadata paired
        // with plain response.body bytes.
        "content-encoding": "gzip",
        "content-length": "42",
      },
    })) as unknown as typeof fetch;

    const response = await GET(
      new NextRequest("http://localhost/api/grove/workspaces/workspace-id/sessions/session-id/turns?last=40"),
      context(),
    );

    expect(response.headers.get("content-encoding")).toBeNull();
    expect(response.headers.get("content-length")).toBeNull();
    expect(new Uint8Array(await response.arrayBuffer())).toEqual(body);
  });

  it.each([204, 205, 304])("keeps the null-body guard for status %i", async (status) => {
    // Fetch's Response constructor enforces null bodies for these statuses, so
    // use a structurally valid upstream mock carrying a non-null stream. This
    // fixture can only succeed through the BFF's explicit status guard.
    const upstream = {
      status,
      body: new ReadableStream<Uint8Array>(),
      headers: new Headers({ "content-type": "application/json" }),
    } as Response;
    globalThis.fetch = vi.fn(async () => upstream) as unknown as typeof fetch;

    const response = await GET(
      new NextRequest("http://localhost/api/grove/workspaces/workspace-id/message"),
      context(["workspaces", "workspace-id", "message"]),
    );

    expect(response.status).toBe(status);
    // The source guard has to make this `null`: `new Response(null, { status })`
    // alone does too, so asserting only `response.body` would be vacuous.
    expect(response.body).toBeNull();
    expect(globalThis.fetch).toHaveBeenCalledTimes(1);
  });
});
