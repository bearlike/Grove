import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ThemeProvider } from "next-themes";
import { WorkspaceCard } from "@/components/workspace/card";
import type {
  AgentActivityState,
  CommitSummaryView,
  WorkspaceActivityView,
} from "@/lib/grove/types";

// The ONE card, rebuilt in the ADE language (#155), metadata torn down to one
// `Stat` grammar (#161): a header (the canonical state glyph · title · time), a
// one-line happening-now context, then a META zone of TWO aligned `Stat` rows —
// provenance (branch · ahead · behind · dirty · placement) over activity (turns ·
// tool calls · tokens in/out + Live toggle) — and the prose last-commit line.
// Every stat is a `data-testid="stat"` + `data-stat="<label>"` icon+value that
// renders NOTHING at zero (no "0 ahead 0 behind" noise). State is the leading
// glyph (the rail's own atom), not a badge; a lifecycle StatusBadge returns only
// when it is the signal (no session / orphaned / error). Repo identity is the
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
          questions: [],
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

// Every meta stat shares `data-testid="stat"` and is keyed by `data-stat`; a
// suppressed (zero) stat is absent, so `stat(label)` returns null — the assertion
// for zero-suppression is simply `expect(stat("behind")).toBeNull()`.
function stat(label: string): HTMLElement | null {
  return document.querySelector<HTMLElement>(`[data-stat="${label}"]`);
}

describe("WorkspaceCard (calm ADE card, repo-grouped)", () => {
  it("header: title link + the leading state glyph; no repo chip, no pill on a healthy card", () => {
    r(<WorkspaceCard activity={activity("working")} />);
    expect(screen.getByRole("link", { name: "ship the dashboard" })).toHaveAttribute(
      "href",
      "/w/w1",
    );
    // Repo identity is the section header now — the card has no project chip.
    expect(screen.queryByTestId("project-chip")).toBeNull();
    // State is the leading glyph (the rail's vocabulary), never a second badge.
    const marks = screen.getAllByTestId("state-mark");
    expect(marks[0]).toHaveAttribute("data-state", "working");
    // A healthy sessioned card wears no lifecycle pill.
    expect(screen.queryByTestId("status-badge")).toBeNull();
  });

  it("header: leads with the canonical agent-state mark", () => {
    r(<WorkspaceCard activity={activity("working")} />);
    const marks = screen.getAllByTestId("state-mark");
    // The first state-mark on the card is the header's identity glyph.
    expect(marks[0]).toHaveAttribute("data-state", "working");
  });

  it("header: blocked surfaces as the attention state glyph", () => {
    r(<WorkspaceCard activity={activity("blocked")} />);
    const marks = screen.getAllByTestId("state-mark");
    expect(marks[0]).toHaveAttribute("data-state", "blocked");
    expect(screen.getByTestId("workspace-card")).toHaveAttribute("data-agent-state", "blocked");
  });

  it("header: shows the workspace StatusBadge when there is no agent session", () => {
    r(<WorkspaceCard activity={activity("working", { sessions: [] })} />);
    expect(screen.getByTestId("status-badge")).toBeInTheDocument();
    expect(screen.getByTestId("workspace-card")).toHaveAttribute("data-agent-state", "unknown");
  });

  it("header: status badge also shows on orphaned / error lifecycle", () => {
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

  it("meta: a root workspace shows the placement badge; worktree shows none", () => {
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

  it("context: happening-now prefers current_task on every tier", () => {
    const { rerender } = r(<WorkspaceCard activity={activity("working")} />);
    expect(screen.getByTestId("happening-now")).toHaveTextContent("editing card.tsx");
    rerenderWrapped(rerender, <WorkspaceCard activity={activity("idle")} />);
    expect(screen.getByTestId("happening-now")).toHaveTextContent("editing card.tsx");
  });

  it("context: happening-now falls back to the session self-name, then a placeholder", () => {
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

  it("context: happening-now shows the background-subagent count only when > 0", () => {
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

  it("context: error_detail takes the slot while in error", () => {
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

  it("meta: activity stats — turns, tool calls, compact tokens in/out", () => {
    r(<WorkspaceCard activity={activity("working")} />);
    // Raw fields render as discrete icon+value stats; tokens fold to the compact
    // token shape (1500 → 1.5k), the rest pass through unchanged.
    expect(stat("turns")).toHaveTextContent("3");
    expect(stat("tool calls")).toHaveTextContent("11");
    expect(stat("tokens in")).toHaveTextContent("1.5k");
    expect(stat("tokens out")).toHaveTextContent("150");
    // The unit is the tooltip / a11y name, never a sigil baked into the row.
    expect(stat("tokens in")).toHaveAttribute("title", "1.5k tokens in");
  });

  it("meta: the live token block (#181) replaces the cumulative stats while generating, hidden until then", () => {
    // No `live` block on the wire yet (pre-#177 daemon / no fast side-channel):
    // the cumulative tokens-in/out stats render exactly as before.
    const { rerender } = r(<WorkspaceCard activity={activity("working")} />);
    expect(screen.queryByTestId("live-token-flow")).toBeNull();
    expect(stat("tokens in")).toHaveTextContent("1.5k");

    // Once a live tier reports, the same slot swaps to the in-flight readout
    // and the cumulative stats step aside — never both at once.
    rerenderWrapped(
      rerender,
      <WorkspaceCard
        activity={activity(
          "working",
          {},
          { live: { tokens_in: 42, tokens_out: 7, generating_since: "2026-06-08T10:00:30Z" } },
        )}
      />,
    );
    const live = screen.getByTestId("live-token-flow");
    expect(live).toHaveTextContent("42");
    expect(live).toHaveTextContent("7");
    expect(stat("tokens in")).toBeNull();

    // Settling back to idle (no live tier) restores the cumulative stats.
    rerenderWrapped(rerender, <WorkspaceCard activity={activity("working")} />);
    expect(screen.queryByTestId("live-token-flow")).toBeNull();
    expect(stat("tokens in")).toHaveTextContent("1.5k");
  });

  it("meta: a sessionless card suppresses every activity stat", () => {
    r(<WorkspaceCard activity={activity("idle", { sessions: [] })} />);
    expect(stat("turns")).toBeNull();
    expect(stat("tool calls")).toBeNull();
    expect(stat("tokens in")).toBeNull();
    expect(stat("tokens out")).toBeNull();
  });

  it("meta: a Live toggle shows only while the agent is working", () => {
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

  it("meta: provenance stats — branch, ahead & dirty render, behind zero-suppressed", () => {
    r(<WorkspaceCard activity={activity("working")} />); // ahead 2, behind 0, dirty 1
    expect(screen.getByText("feat/dash")).toBeInTheDocument();
    expect(stat("ahead")).toHaveTextContent("2");
    expect(stat("dirty")).toHaveTextContent("1");
    // behind is 0 → the atom renders nothing (no "0 behind" noise on the row).
    expect(stat("behind")).toBeNull();
  });

  it("meta: zero-suppression — a fresh workspace with 0-ahead renders no ahead stat", () => {
    r(<WorkspaceCard activity={activity("working", { base_ahead: 0 })} />);
    expect(stat("ahead")).toBeNull();
    // Its nonzero neighbours still render — suppression is per-stat, not per-row.
    expect(stat("dirty")).toHaveTextContent("1");
  });

  it("meta: the parsed last commit (no emoji), latest only", () => {
    const { rerender } = r(<WorkspaceCard activity={activity("working")} />);
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
