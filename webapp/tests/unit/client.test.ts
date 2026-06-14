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

  it("getSessions calls /api/grove/workspaces/{id}/sessions (limit optional)", async () => {
    // A fresh Response per call — a Response body is single-read.
    const fetchMock = vi.fn().mockImplementation(async () =>
      new Response(JSON.stringify([]), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const client = GroveClient.default();
    await client.getSessions("w1");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/grove/workspaces/w1/sessions",
      expect.objectContaining({ method: "GET" }),
    );

    await client.getSessions("w1", 5);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/grove/workspaces/w1/sessions?limit=5",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("getSessionTurns calls /api/grove/workspaces/{id}/sessions/{sid}/turns?last=N", async () => {
    const detail = { session: { session_id: "s1" }, turns: [] };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(detail), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const client = GroveClient.default();
    const result = await client.getSessionTurns("w1", "s1", 100);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/grove/workspaces/w1/sessions/s1/turns?last=100",
      expect.objectContaining({ method: "GET" }),
    );
    expect(result).toEqual(detail);
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

  it("sendMessage POSTs {text} and tolerates the empty 204", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    const client = GroveClient.default();
    await expect(client.sendMessage("w1", "do the thing")).resolves.toBeUndefined();

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/grove/workspaces/w1/message",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ text: "do the thing" }),
      }),
    );
  });

  it("interrupt POSTs with no body and maps a 409 refusal to a typed error", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ detail: { error: "agent_not_running", message: "nothing to stop" } }),
          { status: 409, headers: { "content-type": "application/json" } },
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    const client = GroveClient.default();
    await expect(client.interrupt("w1")).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/grove/workspaces/w1/interrupt",
      expect.objectContaining({ method: "POST" }),
    );

    await expect(client.interrupt("w1")).rejects.toMatchObject({
      name: "GroveProtocolError",
      code: "agent_not_running",
      status: 409,
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

describe("GroveClient lifecycle mutations (#56)", () => {
  const stateBody = (id: string) => ({ id, title: id, status: "running" });

  function stubJson(body: unknown, status = 200) {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(body), {
        status,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  it("createWorkspace POSTs the request to /workspaces and returns the state", async () => {
    const fetchMock = stubJson(stateBody("w-new"));
    const client = GroveClient.default();
    const req = {
      agent_name: "claude",
      title: "my task",
      branch_plan: { kind: "auto", base_ref: "HEAD" },
      skip_init: false,
      initial_prompt: null,
      repo_root: "/repo",
    } as const;

    const result = await client.createWorkspace(req);
    expect(result).toMatchObject({ id: "w-new" });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/grove/workspaces",
      expect.objectContaining({ method: "POST", body: JSON.stringify(req) }),
    );
  });

  it("pauseWorkspace POSTs {force} and returns the updated state", async () => {
    const fetchMock = stubJson(stateBody("w1"));
    const client = GroveClient.default();
    await client.pauseWorkspace("w1", true);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/grove/workspaces/w1/pause",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ force: true }) }),
    );
  });

  it("resumeWorkspace and respawnWorkspace POST with no body", async () => {
    // A fresh Response per call — a Response body is single-read.
    const fetchMock = vi.fn().mockImplementation(async () =>
      new Response(JSON.stringify(stateBody("w1")), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const client = GroveClient.default();
    await client.resumeWorkspace("w1");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/grove/workspaces/w1/resume",
      expect.objectContaining({ method: "POST" }),
    );
    expect((fetchMock.mock.calls[0][1] as RequestInit).body).toBeUndefined();

    await client.respawnWorkspace("w1");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/grove/workspaces/w1/respawn",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("killWorkspace POSTs {delete_branch} and tolerates the empty 204", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    const client = GroveClient.default();

    await expect(client.killWorkspace("w1", true)).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/grove/workspaces/w1/kill",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ delete_branch: true }) }),
    );

    await client.killWorkspace("w1", null);
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/grove/workspaces/w1/kill",
      expect.objectContaining({ body: JSON.stringify({ delete_branch: null }) }),
    );
  });

  it("listBranches and listAgents GET with repo/scope query params", async () => {
    const fetchMock = vi.fn().mockImplementation(async () =>
      new Response(JSON.stringify([]), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const client = GroveClient.default();

    await client.listBranches("/my repo", "local");
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/grove/branches?repo=%2Fmy%20repo&scope=local",
      expect.objectContaining({ method: "GET" }),
    );

    await client.listAgents("/repo");
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/grove/agents?repo=%2Frepo",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("create surfaces a branch_conflict refusal as a typed error", async () => {
    stubJson({ detail: { error: "branch_conflict", message: "branch exists" } }, 409);
    const client = GroveClient.default();
    await expect(
      client.createWorkspace({
        agent_name: "claude",
        title: "x",
        skip_init: false,
      } as never),
    ).rejects.toMatchObject({ name: "GroveProtocolError", code: "branch_conflict", status: 409 });
  });
});
