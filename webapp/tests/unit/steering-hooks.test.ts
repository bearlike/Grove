import { describe, it, expect, vi, beforeEach } from "vitest";
import { createElement, type ReactNode } from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useInterrupt, useSendMessage } from "@/lib/grove/hooks";
import type { SessionDetailView } from "@/lib/grove/types";

// .ts on purpose (the unit include pattern), so the provider wrapper uses
// createElement instead of JSX.
function wrapperFor(queryClient: QueryClient) {
  return ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: queryClient }, children);
}

function newQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

const SEED: SessionDetailView = {
  session: { session_id: "s1" } as SessionDetailView["session"],
  turns: [
    {
      user_text: "build it",
      started_at: "2026-06-11T10:00:00Z",
      entries: [{ role: "assistant", text: "ok" }],
    },
  ],
};

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("useSendMessage", () => {
  it("POSTs {text} to /workspaces/{id}/message and optimistically appends a user turn", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    const queryClient = newQueryClient();
    queryClient.setQueryData(["turns", "w1", "s1"], SEED);

    const { result } = renderHook(() => useSendMessage("w1", "s1"), {
      wrapper: wrapperFor(queryClient),
    });
    result.current.mutate("run the tests");

    // The optimistic row lands before the request resolves.
    await waitFor(() => {
      const data = queryClient.getQueryData<SessionDetailView>(["turns", "w1", "s1"]);
      expect(data?.turns).toHaveLength(2);
      expect(data?.turns[1].user_text).toBe("run the tests");
      expect(data?.turns[1].entries).toEqual([]);
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/grove/workspaces/w1/message",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ text: "run the tests" }) }),
    );
  });

  it("skips the optimistic write when there is no session yet", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    const queryClient = newQueryClient();
    const { result } = renderHook(() => useSendMessage("w1", null), {
      wrapper: wrapperFor(queryClient),
    });
    result.current.mutate("hello");

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(queryClient.getQueryData(["turns", "w1", null])).toBeUndefined();
  });

  it("surfaces the daemon refusal envelope as a typed error", async () => {
    const refusal = { detail: { error: "agent_not_running", message: "no running agent" } };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(refusal), {
          status: 409,
          headers: { "content-type": "application/json" },
        }),
      ),
    );

    const queryClient = newQueryClient();
    queryClient.setQueryData(["turns", "w1", "s1"], SEED);
    const { result } = renderHook(() => useSendMessage("w1", "s1"), {
      wrapper: wrapperFor(queryClient),
    });
    result.current.mutate("hello");

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toMatchObject({
      name: "GroveProtocolError",
      code: "agent_not_running",
      status: 409,
    });
  });
});

describe("useInterrupt", () => {
  it("POSTs to /workspaces/{id}/interrupt with no body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    const queryClient = newQueryClient();
    const { result } = renderHook(() => useInterrupt("w1"), {
      wrapper: wrapperFor(queryClient),
    });
    result.current.mutate();

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/grove/workspaces/w1/interrupt",
      expect.objectContaining({ method: "POST" }),
    );
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.body).toBeUndefined();
  });
});
