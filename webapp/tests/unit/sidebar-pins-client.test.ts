import { afterEach, describe, expect, it, vi } from "vitest";
import { GroveClient, GroveProtocolError } from "@/lib/grove/api";

afterEach(() => vi.unstubAllGlobals());

describe("sidebar pin client", () => {
  it("reads the shared backend list and forwards cancellation", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json(["second", "first"]));
    vi.stubGlobal("fetch", fetcher);
    const signal = new AbortController().signal;
    expect(await GroveClient.create().getSidebarPins(signal)).toEqual(["second", "first"]);
    expect(fetcher).toHaveBeenCalledWith("/api/grove/sidebar/pins", expect.objectContaining({ method: "GET", signal }));
  });

  it("uses idempotent pin and unpin verbs and adopts server ordering", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(Response.json(["new", "old"]))
      .mockResolvedValueOnce(Response.json(["old"]));
    vi.stubGlobal("fetch", fetcher);
    const client = GroveClient.create();
    expect(await client.setSidebarPin("new", true)).toEqual(["new", "old"]);
    expect(await client.setSidebarPin("new", false)).toEqual(["old"]);
    expect(fetcher).toHaveBeenNthCalledWith(1, "/api/grove/sidebar/pins/new", expect.objectContaining({ method: "PUT" }));
    expect(fetcher).toHaveBeenNthCalledWith(2, "/api/grove/sidebar/pins/new", expect.objectContaining({ method: "DELETE" }));
  });

  it("surfaces a backend refusal instead of inventing local pin state", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(
      { detail: { error: "workspace_not_found", message: "Session was removed" } },
      { status: 404 },
    )));
    await expect(GroveClient.create().setSidebarPin("removed", true)).rejects.toBeInstanceOf(GroveProtocolError);
  });
});
