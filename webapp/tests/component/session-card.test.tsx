import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ThemeProvider } from "next-themes";
import { SessionCard } from "@/components/dashboard/session-card";
import type {
  AgentActivityState,
  CommitSummaryView,
  WorkspaceActivityView,
} from "@/lib/grove/types";

const SAMPLE_COMMITS: CommitSummaryView[] = [
  { sha: "abc1234def", subject: "wire the SSE stream", committed_at: "2026-06-08T10:00:00Z" },
  { sha: "9988776655", subject: "add the activity tier", committed_at: "2026-06-08T09:00:00Z" },
  { sha: "1122334455", subject: "seed the dashboard", committed_at: "2026-06-08T08:00:00Z" },
];

function activity(
  state: AgentActivityState,
  over: Partial<WorkspaceActivityView> = {},
  actOver: Partial<WorkspaceActivityView["sessions"][number]["activity"]> = {},
): WorkspaceActivityView {
  const attention = state === "waiting" || state === "blocked" || state === "error";
  return {
    state: {
      id: "w1",
      title: "ship the dashboard",
      repo_root: "/repos/Grove",
      branch: "feat/dash",
      base_branch: "main",
      worktree_path: "/x",
      tmux_session: "grove-x",
      agent_name: "claude",
      status: "active",
      created_at: "2026-06-01T00:00:00Z",
      updated_at: "2026-06-01T00:00:00Z",
    } as WorkspaceActivityView["state"],
    sessions: [
      {
        session: {
          session_id: "s1",
          adapter_kind: "claude_code",
          provenance: "grove_launched",
          tmux_window: "agent",
        },
        activity: {
          state,
          title: "ai: wiring the SSE stream",
          current_task: "editing session-card.tsx",
          human_turns: 3,
          assistant_replies: 7,
          replies_per_turn: [3, 2, 2],
          tool_calls: 11,
          model: "claude-opus-4-8",
          tokens_in: 1500,
          tokens_out: 150,
          last_event_at: null,
          needs_attention: attention,
          error_detail: null,
          ...actOver,
        },
      },
    ],
    base_ahead: 2,
    base_behind: 0,
    diff_added: 40,
    diff_removed: 5,
    dirty_files: 1,
    pane_target: "grove-x:agent",
    needs_attention: attention,
    recent_commits: SAMPLE_COMMITS,
    observed_at: "2026-06-08T10:01:00Z",
    ...over,
  };
}

function r(node: React.ReactNode) {
  return render(
    <ThemeProvider attribute="class" defaultTheme="dark">
      {node}
    </ThemeProvider>,
  );
}

describe("SessionCard", () => {
  it("renders title, branch, agent, state label, and counts", () => {
    r(<SessionCard activity={activity("working")} />);
    expect(screen.getByText("ship the dashboard")).toBeInTheDocument();
    expect(screen.getByText("feat/dash")).toBeInTheDocument();
    expect(screen.getByText("claude")).toBeInTheDocument();
    expect(screen.getByTestId("agent-state-label")).toHaveTextContent("working");
    expect(screen.getByText("claude-opus-4-8")).toBeInTheDocument();
    expect(screen.getByTestId("status-badge")).toBeInTheDocument();
  });

  it("leads with the last commit (subject + sha), durable not ephemeral", () => {
    r(<SessionCard activity={activity("working")} />);
    const lastCommit = screen.getByTestId("last-commit");
    expect(lastCommit).toHaveTextContent("wire the SSE stream");
    expect(lastCommit).toHaveTextContent("abc1234"); // shortened sha
  });

  it("shows a muted 'no commits yet' when the worktree has none", () => {
    r(<SessionCard activity={activity("working", { recent_commits: [] })} />);
    expect(screen.getByTestId("last-commit")).toHaveTextContent("no commits yet");
  });

  it("shows up to two more recent commits as compact history", () => {
    r(<SessionCard activity={activity("working")} />);
    const history = screen.getByTestId("commit-history");
    expect(history).toHaveTextContent("add the activity tier");
    expect(history).toHaveTextContent("seed the dashboard");
  });

  it("shows current_task as an active-only ongoing-action line", () => {
    const { rerender } = r(<SessionCard activity={activity("working")} />);
    expect(screen.getByTestId("current-task")).toHaveTextContent("editing session-card.tsx");

    // Dormant / attention tiers hide the live ongoing line.
    rerender(
      <ThemeProvider attribute="class" defaultTheme="dark">
        <SessionCard activity={activity("idle")} />
      </ThemeProvider>,
    );
    expect(screen.queryByTestId("current-task")).toBeNull();
  });

  it("renders the agent brand badge next to the identity", () => {
    r(<SessionCard activity={activity("working")} />);
    // The Claude brand mark resolves to a labelled svg via AgentBadge → AgentGlyph.
    expect(screen.getByLabelText("Claude Code")).toBeInTheDocument();
  });

  it("surfaces the per-card observed_at refresh time", () => {
    r(<SessionCard activity={activity("working")} />);
    expect(screen.getByTestId("observed-at")).toHaveTextContent("updated");
  });

  it("exposes the agent state on the testid seam and links to the detail page", () => {
    r(<SessionCard activity={activity("waiting")} />);
    expect(screen.getByTestId("session-card")).toHaveAttribute("data-agent-state", "waiting");
    expect(screen.getByRole("link", { name: "ship the dashboard" })).toHaveAttribute(
      "href",
      "/w/w1",
    );
  });

  it("shows a Live toggle only while the agent is working", () => {
    const onToggleLive = vi.fn();
    const { rerender } = r(<SessionCard activity={activity("working")} onToggleLive={onToggleLive} />);
    screen.getByTestId("live-toggle").click();
    expect(onToggleLive).toHaveBeenCalledWith("w1");

    rerender(
      <ThemeProvider attribute="class" defaultTheme="dark">
        <SessionCard activity={activity("waiting")} onToggleLive={onToggleLive} />
      </ThemeProvider>,
    );
    expect(screen.queryByTestId("live-toggle")).toBeNull(); // gated to WORKING
  });

  it("shows error_detail only when the state is error and a detail exists", () => {
    const { rerender } = r(
      <SessionCard
        activity={activity("error", {}, { error_detail: "transcript unreadable: bad JSON" })}
      />,
    );
    expect(screen.getByTestId("error-detail")).toHaveTextContent(
      "transcript unreadable: bad JSON",
    );

    // Error state without a detail → no empty row.
    rerender(
      <ThemeProvider attribute="class" defaultTheme="dark">
        <SessionCard activity={activity("error")} />
      </ThemeProvider>,
    );
    expect(screen.queryByTestId("error-detail")).toBeNull();

    // A detail left over from a past failure stays hidden while not in error.
    rerender(
      <ThemeProvider attribute="class" defaultTheme="dark">
        <SessionCard activity={activity("working", {}, { error_detail: "stale" })} />
      </ThemeProvider>,
    );
    expect(screen.queryByTestId("error-detail")).toBeNull();
  });

  it("shows the durable session title on any tier, preferring interpreted_status", () => {
    // Dormant tier still shows the durable name (only current_task is active-gated).
    const { rerender } = r(<SessionCard activity={activity("idle")} />);
    expect(screen.getByTestId("session-title")).toHaveTextContent(
      "ai: wiring the SSE stream",
    );
    expect(screen.queryByTestId("current-task")).toBeNull();

    rerender(
      <ThemeProvider attribute="class" defaultTheme="dark">
        <SessionCard
          activity={activity("idle", {}, { interpreted_status: "compiling the webapp" })}
        />
      </ThemeProvider>,
    );
    expect(screen.getByTestId("session-title")).toHaveTextContent("compiling the webapp");

    rerender(
      <ThemeProvider attribute="class" defaultTheme="dark">
        <SessionCard
          activity={activity("idle", {}, { title: null, interpreted_status: null })}
        />
      </ThemeProvider>,
    );
    expect(screen.queryByTestId("session-title")).toBeNull();
  });

  it("renders an extra-sessions strip only when more than one session exists", () => {
    const base = activity("working");
    const second: WorkspaceActivityView["sessions"][number] = {
      session: {
        session_id: "s2",
        adapter_kind: "codex",
        provenance: "fs_discovered",
        tmux_window: null,
      },
      activity: { ...base.sessions[0].activity, state: "idle" },
    };

    const { rerender } = r(
      <SessionCard activity={activity("working", { sessions: [...base.sessions, second] })} />,
    );
    const strip = screen.getByTestId("extra-sessions");
    expect(strip).toHaveTextContent("+1 more session");
    expect(strip).toHaveAttribute("href", "/w/w1");

    // Single session → no strip at all.
    rerender(
      <ThemeProvider attribute="class" defaultTheme="dark">
        <SessionCard activity={activity("working")} />
      </ThemeProvider>,
    );
    expect(screen.queryByTestId("extra-sessions")).toBeNull();
  });

  it("dims a dormant card and keeps an attention card full-opacity + highlighted", () => {
    const { rerender } = r(<SessionCard activity={activity("idle")} />);
    const card = () => screen.getByTestId("session-card");
    // Dormant → reduced opacity, no highlight ring.
    expect(card().getAttribute("data-tier")).toBe("dormant");
    expect(card().className).toMatch(/opacity-(70|55)/);

    // Attention → full opacity, highlight ring (never dimmed).
    rerender(
      <ThemeProvider attribute="class" defaultTheme="dark">
        <SessionCard activity={activity("waiting")} />
      </ThemeProvider>,
    );
    expect(card().getAttribute("data-tier")).toBe("attention");
    expect(card().className).toContain("opacity-100");
    expect(card().className).toContain("ring-2");

    // Active → full opacity.
    rerender(
      <ThemeProvider attribute="class" defaultTheme="dark">
        <SessionCard activity={activity("working")} />
      </ThemeProvider>,
    );
    expect(card().getAttribute("data-tier")).toBe("active");
    expect(card().className).toContain("opacity-100");
  });
});
