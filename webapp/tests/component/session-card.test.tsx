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
          active_subagents: 0,
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

function rerenderWrapped(rerender: (ui: React.ReactNode) => void, node: React.ReactNode) {
  rerender(
    <ThemeProvider attribute="class" defaultTheme="dark">
      {node}
    </ThemeProvider>,
  );
}

describe("SessionCard", () => {
  it("slot (a): title link, project chip, and the agent-state badge", () => {
    r(<SessionCard activity={activity("working")} projectName="Grove" />);
    expect(screen.getByRole("link", { name: "ship the dashboard" })).toHaveAttribute(
      "href",
      "/w/w1",
    );
    expect(screen.getByTestId("project-chip")).toHaveTextContent("Grove");
    const badge = screen.getByTestId("agent-state-badge");
    expect(badge).toHaveAttribute("data-state", "working");
    expect(badge).toHaveAttribute("aria-label", "agent state: working");
    expect(screen.getByTestId("agent-state-label")).toHaveTextContent("working");
    // The agent axis replaces the lifecycle badge on a healthy sessioned card.
    expect(screen.queryByTestId("status-badge")).toBeNull();
  });

  it("slot (a): no project chip when the wall doesn't pass one", () => {
    r(<SessionCard activity={activity("working")} />);
    expect(screen.queryByTestId("project-chip")).toBeNull();
  });

  it("slot (a): blocked reads 'action required' — the loudest badge", () => {
    r(<SessionCard activity={activity("blocked")} />);
    const badge = screen.getByTestId("agent-state-badge");
    expect(badge).toHaveAttribute("aria-label", "agent state: action required");
    expect(badge).toHaveTextContent("action required");
  });

  it("slot (a): falls back to the workspace StatusBadge when there is no session", () => {
    r(<SessionCard activity={activity("working", { sessions: [] })} />);
    expect(screen.getByTestId("status-badge")).toBeInTheDocument();
    expect(screen.queryByTestId("agent-state-badge")).toBeNull();
    expect(screen.getByTestId("session-card")).toHaveAttribute("data-agent-state", "unknown");
  });

  it("slot (a): renders the agent brand badge next to the identity", () => {
    r(<SessionCard activity={activity("working")} />);
    // The Claude brand mark resolves to a labelled svg via AgentBadge → AgentGlyph.
    expect(screen.getByLabelText("Claude Code")).toBeInTheDocument();
  });

  it("slot (b): happening-now prefers current_task on every tier", () => {
    const { rerender } = r(<SessionCard activity={activity("working")} />);
    expect(screen.getByTestId("happening-now")).toHaveTextContent("editing session-card.tsx");

    // A dormant card still shows its last-known task — the glance line never vanishes.
    rerenderWrapped(rerender, <SessionCard activity={activity("idle")} />);
    expect(screen.getByTestId("happening-now")).toHaveTextContent("editing session-card.tsx");
  });

  it("slot (b): falls back to the durable session self-name, then a quiet placeholder", () => {
    const { rerender } = r(
      <SessionCard activity={activity("idle", {}, { current_task: null })} />,
    );
    expect(screen.getByTestId("happening-now")).toHaveTextContent("ai: wiring the SSE stream");

    rerenderWrapped(
      rerender,
      <SessionCard activity={activity("idle", {}, { current_task: null, title: null })} />,
    );
    expect(screen.getByTestId("happening-now")).toHaveTextContent("no activity yet");

    rerenderWrapped(rerender, <SessionCard activity={activity("idle", { sessions: [] })} />);
    expect(screen.getByTestId("happening-now")).toHaveTextContent("no agent session");
  });

  it("slot (b): shows the background-subagent count only when > 0", () => {
    const { rerender } = r(
      <SessionCard activity={activity("working", {}, { active_subagents: 2 })} />,
    );
    expect(screen.getByTestId("happening-now")).toHaveTextContent("· 2 bg agents");

    rerenderWrapped(
      rerender,
      <SessionCard activity={activity("working", {}, { active_subagents: 1 })} />,
    );
    expect(screen.getByTestId("happening-now")).toHaveTextContent("· 1 bg agent");
    expect(screen.getByTestId("happening-now")).not.toHaveTextContent("bg agents");

    rerenderWrapped(rerender, <SessionCard activity={activity("working")} />);
    expect(screen.getByTestId("happening-now")).not.toHaveTextContent("bg agent");
  });

  it("slot (b): error_detail takes the happening-now slot while in error", () => {
    const { rerender } = r(
      <SessionCard
        activity={activity("error", {}, { error_detail: "transcript unreadable: bad JSON" })}
      />,
    );
    expect(screen.getByTestId("happening-now")).toHaveTextContent(
      "transcript unreadable: bad JSON",
    );

    // A detail left over from a past failure stays hidden while not in error.
    rerenderWrapped(
      rerender,
      <SessionCard activity={activity("working", {}, { error_detail: "stale" })} />,
    );
    expect(screen.getByTestId("happening-now")).toHaveTextContent("editing session-card.tsx");
  });

  it("slot (c): muted metrics one-liner — turns, tools, tokens", () => {
    const { rerender } = r(<SessionCard activity={activity("working")} />);
    expect(screen.getByTestId("metrics")).toHaveTextContent("3t · 11⚒ · 1.5k↑ 150↓");

    rerenderWrapped(rerender, <SessionCard activity={activity("idle", { sessions: [] })} />);
    expect(screen.getByTestId("metrics")).toHaveTextContent("—");
  });

  it("slot (d): last commit subject + relative time, or a quiet empty note", () => {
    const { rerender } = r(<SessionCard activity={activity("working")} />);
    expect(screen.getByTestId("last-commit")).toHaveTextContent("wire the SSE stream");
    // Only the LATEST commit shows — no history list on the dense card.
    expect(screen.getByTestId("session-card")).not.toHaveTextContent("add the activity tier");

    rerenderWrapped(
      rerender,
      <SessionCard activity={activity("working", { recent_commits: [] })} />,
    );
    expect(screen.getByTestId("last-commit")).toHaveTextContent("no commits yet");
  });

  it("exposes the agent state and tier on the testid seam", () => {
    r(<SessionCard activity={activity("waiting")} />);
    const card = screen.getByTestId("session-card");
    expect(card).toHaveAttribute("data-agent-state", "waiting");
    expect(card).toHaveAttribute("data-tier", "attention");
  });

  it("shows a Live toggle only while the agent is working", () => {
    const onToggleLive = vi.fn();
    const { rerender } = r(
      <SessionCard activity={activity("working")} onToggleLive={onToggleLive} />,
    );
    screen.getByTestId("live-toggle").click();
    expect(onToggleLive).toHaveBeenCalledWith("w1");

    rerenderWrapped(
      rerender,
      <SessionCard activity={activity("waiting")} onToggleLive={onToggleLive} />,
    );
    expect(screen.queryByTestId("live-toggle")).toBeNull(); // gated to WORKING
  });

  it("dims a dormant card and keeps an attention card full-opacity + highlighted", () => {
    const { rerender } = r(<SessionCard activity={activity("idle")} />);
    const card = () => screen.getByTestId("session-card");
    // Dormant → reduced opacity, no highlight ring.
    expect(card().getAttribute("data-tier")).toBe("dormant");
    expect(card().className).toMatch(/opacity-(70|55)/);

    // Attention → full opacity, highlight ring (never dimmed).
    rerenderWrapped(rerender, <SessionCard activity={activity("waiting")} />);
    expect(card().getAttribute("data-tier")).toBe("attention");
    expect(card().className).toContain("opacity-100");
    expect(card().className).toContain("ring-2");

    // Active → full opacity.
    rerenderWrapped(rerender, <SessionCard activity={activity("working")} />);
    expect(card().getAttribute("data-tier")).toBe("active");
    expect(card().className).toContain("opacity-100");
  });
});
