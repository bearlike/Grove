import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SessionsPanel } from "@/components/workspace/sessions-panel";
import type { SessionSummaryView } from "@/lib/grove/types";

function session(over: Partial<SessionSummaryView> = {}): SessionSummaryView {
  return {
    session_id: "s1",
    adapter_kind: "claude_code",
    provenance: "grove_launched",
    workspace_id: "w1",
    git_branch: "feat/depth",
    created_at: "2026-06-10T10:00:00Z",
    modified_at: "2026-06-10T11:00:00Z",
    size_bytes: 4096,
    title: "wire the sessions panel",
    first_prompt: "build the sessions panel",
    last_prompt: "now the tests",
    activity: {
      state: "working",
      title: "ai: wiring the panel",
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
    ...over,
  };
}

const TURNS_DETAIL = {
  session: session(),
  turns: [
    {
      user_text: "build the sessions panel",
      started_at: "2026-06-10T10:00:00Z",
      entries: [{ role: "assistant", text: "On it." }],
    },
  ],
};

// Real code path through useSessionTurns — stub only the fetch boundary.
function stubTurnsFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify(TURNS_DETAIL), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    ),
  );
}

function r(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>);
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

describe("SessionsPanel", () => {
  it("renders one row per session with title, state, and metrics", () => {
    r(
      <SessionsPanel
        workspaceId="w1"
        sessions={[session(), session({ session_id: "s2", title: null })]}
      />,
    );
    const rows = screen.getAllByTestId("session-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveAttribute("data-session-id", "s1");
    expect(rows[0]).toHaveTextContent("wire the sessions panel");
    expect(rows[0]).toHaveTextContent("working");
    expect(rows[0]).toHaveTextContent("claude-opus-4-8");
    // No title → first_prompt stands in.
    expect(rows[1]).toHaveTextContent("build the sessions panel");
  });

  it("maps provenance to quiet labels: grove / hand-started", () => {
    r(
      <SessionsPanel
        workspaceId="w1"
        sessions={[
          session(),
          session({ session_id: "s2", provenance: "fs_discovered" }),
        ]}
      />,
    );
    const tags = screen.getAllByTestId("session-provenance");
    expect(tags[0]).toHaveTextContent("grove");
    expect(tags[1]).toHaveTextContent("hand-started");
  });

  it("degrades to an empty state instead of hiding the panel", () => {
    r(<SessionsPanel workspaceId="w1" sessions={[]} />);
    expect(screen.getByTestId("sessions-panel")).toHaveTextContent(
      "no recorded sessions",
    );
  });

  it("expands a clicked row into its turns view and collapses on re-click", async () => {
    stubTurnsFetch();
    const user = userEvent.setup();
    r(<SessionsPanel workspaceId="w1" sessions={[session()]} />);

    expect(screen.queryByTestId("turns-view")).toBeNull();
    await user.click(screen.getByTestId("session-row"));
    expect(await screen.findByTestId("turns-view")).toBeInTheDocument();
    expect(screen.getByTestId("session-row")).toHaveAttribute("aria-expanded", "true");

    await user.click(screen.getByTestId("session-row"));
    expect(screen.queryByTestId("turns-view")).toBeNull();
  });

  it("keeps at most one row expanded at a time", async () => {
    stubTurnsFetch();
    const user = userEvent.setup();
    r(
      <SessionsPanel
        workspaceId="w1"
        sessions={[session(), session({ session_id: "s2" })]}
      />,
    );
    const rows = () => screen.getAllByTestId("session-row");

    await user.click(rows()[0]);
    expect(rows()[0]).toHaveAttribute("aria-expanded", "true");
    await user.click(rows()[1]);
    expect(rows()[0]).toHaveAttribute("aria-expanded", "false");
    expect(rows()[1]).toHaveAttribute("aria-expanded", "true");
    expect(screen.getAllByTestId("turns-view")).toHaveLength(1);
  });
});
