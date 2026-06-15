import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ThemeProvider } from "next-themes";
import { WorkspaceCard } from "@/components/workspace/card";
import type {
  AgentActivityState,
  CommitSummaryView,
  WorkspaceActivityView,
} from "@/lib/grove/types";

// The ONE card (issue #89, redesigned #96): two scan lines — identity (state
// mark · title · one status pill) and prompt (happening-now) — plus a demoted
// footer (metrics · branch · diff · last commit). Repo identity moved up to the
// grid section header, so the card carries no project chip. It reads
// `WorkspaceActivityView` directly.

const SAMPLE_COMMITS: CommitSummaryView[] = [
  { sha: "abc1234def", subject: "✨ feat: wire the SSE stream", committed_at: "2026-06-08T10:00:00Z" },
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
      placement: "worktree",
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
          current_task: "editing card.tsx",
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

describe("WorkspaceCard (two-line, repo-grouped redesign)", () => {
  it("line 1: title link + the agent-state badge; no repo chip", () => {
    r(<WorkspaceCard activity={activity("working")} />);
    expect(screen.getByRole("link", { name: "ship the dashboard" })).toHaveAttribute(
      "href",
      "/w/w1",
    );
    // Repo identity is the section header now — the card has no project chip.
    expect(screen.queryByTestId("project-chip")).toBeNull();
    const badge = screen.getByTestId("agent-state-badge");
    expect(badge).toHaveAttribute("data-state", "working");
    expect(screen.getByTestId("agent-state-label")).toHaveTextContent("working");
    // The agent axis replaces the lifecycle badge on a healthy sessioned card.
    expect(screen.queryByTestId("status-badge")).toBeNull();
  });

  it("line 1: leads with the canonical agent-state mark", () => {
    r(<WorkspaceCard activity={activity("working")} />);
    const marks = screen.getAllByTestId("state-mark");
    // The first state-mark on the card is line 1's identity glyph for the state.
    expect(marks[0]).toHaveAttribute("data-state", "working");
  });

  it("line 1: blocked reads 'action required'", () => {
    r(<WorkspaceCard activity={activity("blocked")} />);
    expect(screen.getByTestId("agent-state-badge")).toHaveTextContent("action required");
  });

  it("line 1: falls back to the workspace StatusBadge when there is no session", () => {
    r(<WorkspaceCard activity={activity("working", { sessions: [] })} />);
    expect(screen.getByTestId("status-badge")).toBeInTheDocument();
    expect(screen.queryByTestId("agent-state-badge")).toBeNull();
    expect(screen.getByTestId("workspace-card")).toHaveAttribute("data-agent-state", "unknown");
  });

  it("line 1: status badge also shows on orphaned / error lifecycle", () => {
    r(<WorkspaceCard activity={activity("working", { state: { ...activity("working").state, status: "orphaned" } })} />);
    expect(screen.getByTestId("status-badge")).toBeInTheDocument();
  });

  it("data-status mirrors the workspace status", () => {
    r(
      <WorkspaceCard
        activity={activity("working", {
          state: { ...activity("working").state, status: "orphaned" },
        })}
      />,
    );
    expect(screen.getByTestId("workspace-card").dataset.status).toBe("orphaned");
  });

  it("footer: a root workspace shows the placement badge; worktree shows none", () => {
    const { rerender } = r(
      <WorkspaceCard
        activity={activity("working", {
          state: { ...activity("working").state, placement: "root" },
        })}
      />,
    );
    const badge = screen.getByTestId("placement-badge");
    expect(badge.dataset.placement).toBe("root");
    expect(badge).toHaveTextContent("root");

    rerenderWrapped(rerender, <WorkspaceCard activity={activity("working")} />);
    expect(screen.queryByTestId("placement-badge")).toBeNull();
  });

  it("line 2: happening-now prefers current_task on every tier", () => {
    const { rerender } = r(<WorkspaceCard activity={activity("working")} />);
    expect(screen.getByTestId("happening-now")).toHaveTextContent("editing card.tsx");
    rerenderWrapped(rerender, <WorkspaceCard activity={activity("idle")} />);
    expect(screen.getByTestId("happening-now")).toHaveTextContent("editing card.tsx");
  });

  it("line 2: happening-now falls back to the session self-name, then a placeholder", () => {
    const { rerender } = r(
      <WorkspaceCard activity={activity("idle", {}, { current_task: null })} />,
    );
    expect(screen.getByTestId("happening-now")).toHaveTextContent("ai: wiring the SSE stream");

    rerenderWrapped(
      rerender,
      <WorkspaceCard activity={activity("idle", {}, { current_task: null, title: null })} />,
    );
    expect(screen.getByTestId("happening-now")).toHaveTextContent("no activity yet");

    rerenderWrapped(rerender, <WorkspaceCard activity={activity("idle", { sessions: [] })} />);
    expect(screen.getByTestId("happening-now")).toHaveTextContent("no agent session");
  });

  it("line 2: happening-now shows the background-subagent count only when > 0", () => {
    const { rerender } = r(
      <WorkspaceCard activity={activity("working", {}, { active_subagents: 2 })} />,
    );
    expect(screen.getByTestId("happening-now")).toHaveTextContent("· 2 bg agents");

    rerenderWrapped(
      rerender,
      <WorkspaceCard activity={activity("working", {}, { active_subagents: 1 })} />,
    );
    expect(screen.getByTestId("happening-now")).toHaveTextContent("· 1 bg agent");
    expect(screen.getByTestId("happening-now")).not.toHaveTextContent("bg agents");

    rerenderWrapped(rerender, <WorkspaceCard activity={activity("working")} />);
    expect(screen.getByTestId("happening-now")).not.toHaveTextContent("bg agent");
  });

  it("line 2: error_detail takes the slot while in error", () => {
    const { rerender } = r(
      <WorkspaceCard
        activity={activity("error", {}, { error_detail: "transcript unreadable: bad JSON" })}
      />,
    );
    expect(screen.getByTestId("happening-now")).toHaveTextContent("transcript unreadable: bad JSON");

    rerenderWrapped(
      rerender,
      <WorkspaceCard activity={activity("working", {}, { error_detail: "stale" })} />,
    );
    expect(screen.getByTestId("happening-now")).toHaveTextContent("editing card.tsx");
  });

  it("footer: metrics one-liner — turns, tools, tokens", () => {
    const { rerender } = r(<WorkspaceCard activity={activity("working")} />);
    expect(screen.getByTestId("metrics")).toHaveTextContent("3t · 11⚒ · 1.5k↑ 150↓");

    rerenderWrapped(rerender, <WorkspaceCard activity={activity("idle", { sessions: [] })} />);
    expect(screen.getByTestId("metrics")).toHaveTextContent("—");
  });

  it("footer: a Live toggle shows only while the agent is working", () => {
    const onToggleLive = vi.fn();
    const { rerender } = r(
      <WorkspaceCard activity={activity("working")} onToggleLive={onToggleLive} />,
    );
    screen.getByTestId("live-toggle").click();
    expect(onToggleLive).toHaveBeenCalledWith("w1");

    rerenderWrapped(
      rerender,
      <WorkspaceCard activity={activity("waiting")} onToggleLive={onToggleLive} />,
    );
    expect(screen.queryByTestId("live-toggle")).toBeNull(); // gated to WORKING
  });

  it("footer: branch, the diff stat trio, and the parsed last commit (no emoji)", () => {
    const { rerender } = r(<WorkspaceCard activity={activity("working")} />);
    expect(screen.getByText("feat/dash")).toBeInTheDocument();
    expect(screen.getByTestId("stat-trio")).toBeInTheDocument();
    expect(screen.getByTestId("stat-ahead")).toHaveTextContent("2");
    expect(screen.getByTestId("stat-behind")).toHaveTextContent("0");
    expect(screen.getByTestId("stat-dirty")).toHaveTextContent("1");
    // The gitmoji is stripped; the conventional type surfaces as a muted tag.
    const lastCommit = screen.getByTestId("last-commit");
    expect(lastCommit).toHaveTextContent("wire the SSE stream");
    expect(lastCommit).toHaveTextContent("feat");
    expect(lastCommit).not.toHaveTextContent("✨");
    // Only the LATEST commit shows — no history list on the dense card.
    expect(screen.getByTestId("workspace-card")).not.toHaveTextContent("add the activity tier");

    rerenderWrapped(rerender, <WorkspaceCard activity={activity("working", { recent_commits: [] })} />);
    expect(screen.getByTestId("last-commit")).toHaveTextContent("no commits yet");
  });

  it("exposes agent-state and tier on the testid seam", () => {
    r(<WorkspaceCard activity={activity("waiting")} />);
    const card = screen.getByTestId("workspace-card");
    expect(card).toHaveAttribute("data-agent-state", "waiting");
    expect(card).toHaveAttribute("data-tier", "attention");
  });

  it("attention earns a thin left accent bar; the card border stays neutral otherwise", () => {
    const { rerender } = r(<WorkspaceCard activity={activity("idle")} />);
    const card = () => screen.getByTestId("workspace-card");
    // Dormant + active wear no accent bar and no full-card ring/glow.
    expect(card().getAttribute("data-tier")).toBe("dormant");
    expect(card().className).not.toContain("border-l-2");

    rerenderWrapped(rerender, <WorkspaceCard activity={activity("waiting")} />);
    expect(card().getAttribute("data-tier")).toBe("attention");
    expect(card().className).toContain("border-l-2");
    // Never a full-card ring — attention is a small left mark only.
    expect(card().className).not.toContain("ring-2");

    rerenderWrapped(rerender, <WorkspaceCard activity={activity("working")} />);
    expect(card().getAttribute("data-tier")).toBe("active");
    expect(card().className).not.toContain("border-l-2");
  });

  it("liveOpen highlights the focused card with a ring", () => {
    r(<WorkspaceCard activity={activity("working")} liveOpen />);
    expect(screen.getByTestId("workspace-card").className).toContain("ring-2");
  });
});
