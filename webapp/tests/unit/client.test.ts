import { describe, it, expect, vi, beforeEach } from "vitest";
import { GroveClient, GroveProtocolError } from "@/lib/grove/client";

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("GroveClient", () => {
  it("listWorkspaces calls /api/grove/workspaces", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify([]), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const client = GroveClient.default();
    const result = await client.listWorkspaces();

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/grove/workspaces",
      expect.objectContaining({ method: "GET" }),
    );
    expect(result).toEqual([]);
  });

  it("getPeek calls /api/grove/workspaces/{id}/peek", async () => {
    const peek = {
      state: { id: "w1" },
      base_ahead: 0,
      base_behind: 0,
      diff_added: 0,
      diff_removed: 0,
      dirty_files: 0,
      recent_commits: [],
      agent_snapshot: null,
      snapshot_taken_at: null,
    };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(peek), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const client = GroveClient.default();
    await client.getPeek("w1");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/grove/workspaces/w1/peek",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("non-2xx response becomes a typed GroveProtocolError", async () => {
    const errBody = { detail: { error: "workspace_not_found", message: "no workspace with id 'x'" } };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(errBody), {
          status: 404,
          headers: { "content-type": "application/json" },
        }),
      ),
    );

    const client = GroveClient.default();
    await expect(client.getWorkspace("x")).rejects.toMatchObject({
      name: "GroveProtocolError",
      code: "workspace_not_found",
      status: 404,
    });
  });

  it("non-JSON error body still produces a typed error with default code", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("plain text", { status: 500 })),
    );

    const client = GroveClient.default();
    await expect(client.listWorkspaces()).rejects.toBeInstanceOf(GroveProtocolError);
  });
});
