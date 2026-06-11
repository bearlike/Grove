import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TurnsView } from "@/components/workspace/turns-view";
import type { SessionDetailView } from "@/lib/grove/types";

const DETAIL: SessionDetailView = {
  session: {
    session_id: "s1",
    adapter_kind: "claude_code",
    provenance: "grove_launched",
    workspace_id: "w1",
    git_branch: "feat/depth",
    created_at: "2026-06-10T10:00:00Z",
    modified_at: "2026-06-10T11:00:00Z",
    size_bytes: 4096,
    title: "wire the panel",
    first_prompt: "build the panel",
    last_prompt: "run the tests",
    activity: {
      state: "working",
      title: "ai: wiring",
      current_task: null,
      human_turns: 3,
      assistant_replies: 7,
      replies_per_turn: [3, 2, 2],
      tool_calls: 11,
      model: "claude-opus-4-8",
      tokens_in: 1500,
      tokens_out: 150,
      last_event_at: null,
      needs_attention: false,
      error_detail: null,
    },
  },
  turns: [
    // A resumed-session head: no fresh prompt, just carried-over context.
    {
      user_text: "",
      started_at: "2026-06-10T09:00:00Z",
      entries: [{ role: "summary", text: "continued from a prior session" }],
    },
    {
      user_text: "build the panel",
      started_at: "2026-06-10T10:00:00Z",
      entries: [
        { role: "assistant", text: "Starting on the panel." },
        { role: "tool", text: "Edit sessions-panel.tsx" },
      ],
    },
    {
      user_text: "run the tests",
      started_at: "2026-06-10T11:00:00Z",
      entries: [{ role: "status", text: "tests green" }],
    },
  ],
};

function stubFetch(detail: SessionDetailView) {
  const mock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(detail), {
      status: 200,
      headers: { "content-type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", mock);
  return mock;
}

function r(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>);
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

describe("TurnsView", () => {
  it("fetches last=100 and renders turns oldest-first", async () => {
    const mock = stubFetch(DETAIL);
    r(<TurnsView workspaceId="w1" sessionId="s1" />);

    const rows = await screen.findAllByTestId("turn-row");
    expect(mock).toHaveBeenCalledWith(
      "/api/grove/workspaces/w1/sessions/s1/turns?last=100",
      expect.objectContaining({ method: "GET" }),
    );
    expect(rows).toHaveLength(3);
    // Wire order preserved: oldest turn first, newest last.
    expect(rows[1]).toHaveTextContent("build the panel");
    expect(rows[2]).toHaveTextContent("run the tests");
  });

  it("renders an empty-prompt head as a quiet continued-session marker", async () => {
    stubFetch(DETAIL);
    r(<TurnsView workspaceId="w1" sessionId="s1" />);

    const rows = await screen.findAllByTestId("turn-row");
    expect(rows[0]).toHaveTextContent("continued session");
    expect(rows[0]).not.toHaveTextContent("❯");
  });

  it("styles entries by role — tool rows are the mono ⚒ seam", async () => {
    stubFetch(DETAIL);
    r(<TurnsView workspaceId="w1" sessionId="s1" />);

    await screen.findAllByTestId("turn-row");
    const entries = screen.getAllByTestId("turn-entry");
    const tool = entries.find((e) => e.dataset.role === "tool");
    expect(tool).toBeDefined();
    expect(tool).toHaveTextContent("⚒");
    expect(tool).toHaveTextContent("Edit sessions-panel.tsx");
    expect(tool!.className).toContain("font-mono");

    const assistant = entries.find((e) => e.dataset.role === "assistant");
    expect(assistant!.className).not.toContain("font-mono");
  });

  it("shows the error state when the turns fetch fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("boom")));
    r(<TurnsView workspaceId="w1" sessionId="s1" />);

    expect(await screen.findByText("couldn't load turns")).toBeInTheDocument();
  });
});
