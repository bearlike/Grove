import { describe, it, expect, vi, beforeEach } from "vitest";
import { createElement, type ReactNode } from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useCreateWorkspace, useWorkspaceActions } from "@/lib/grove/hooks";

function wrapperFor(queryClient: QueryClient) {
  return ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: queryClient }, children);
}

function newQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("useWorkspaceActions", () => {
  it("pause POSTs {force} and invalidates the workspace caches on success", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: "w1", status: "paused" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const queryClient = newQueryClient();
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useWorkspaceActions("w1"), {
      wrapper: wrapperFor(queryClient),
    });

    result.current.pause.mutate(false);
    await waitFor(() => expect(result.current.pause.isSuccess).toBe(true));

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/grove/workspaces/w1/pause",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ force: false }) }),
    );
    const keys = spy.mock.calls.map((c) => JSON.stringify(c[0]?.queryKey));
    expect(keys).toContain(JSON.stringify(["peek", "w1"]));
    expect(keys).toContain(JSON.stringify(["activity"]));
  });

  it("kill POSTs {delete_branch} from the explicit choice", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    const queryClient = newQueryClient();
    const { result } = renderHook(() => useWorkspaceActions("w1"), {
      wrapper: wrapperFor(queryClient),
    });

    result.current.kill.mutate(true);
    await waitFor(() => expect(result.current.kill.isSuccess).toBe(true));
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/grove/workspaces/w1/kill",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ delete_branch: true }) }),
    );
  });

  it("surfaces a daemon refusal as a typed error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ detail: { error: "workspace_state_error", message: "not running" } }),
          { status: 409, headers: { "content-type": "application/json" } },
        ),
      ),
    );

    const queryClient = newQueryClient();
    const { result } = renderHook(() => useWorkspaceActions("w1"), {
      wrapper: wrapperFor(queryClient),
    });

    result.current.pause.mutate(false);
    await waitFor(() => expect(result.current.pause.isError).toBe(true));
    expect(result.current.pause.error).toMatchObject({
      name: "GroveProtocolError",
      code: "workspace_state_error",
      status: 409,
    });
  });
});

describe("useCreateWorkspace", () => {
  it("POSTs the request to /workspaces and invalidates list + activity", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: "w-new", status: "running" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const queryClient = newQueryClient();
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useCreateWorkspace(), {
      wrapper: wrapperFor(queryClient),
    });

    result.current.mutate({
      agent_name: "claude",
      title: "build it",
      branch_plan: { kind: "auto", base_ref: "HEAD" },
      skip_init: false,
      initial_prompt: null,
      repo_root: "/repo",
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/grove/workspaces",
      expect.objectContaining({ method: "POST" }),
    );
    const keys = spy.mock.calls.map((c) => JSON.stringify(c[0]?.queryKey));
    expect(keys).toContain(JSON.stringify(["workspaces"]));
    expect(keys).toContain(JSON.stringify(["activity"]));
  });
});
