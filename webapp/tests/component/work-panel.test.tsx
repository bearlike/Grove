import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { WorkPanel } from "@/components/workspace/work-panel";
import { AgentLiveStatus } from "@/lib/grove/agent-activity";
import type { AgentActivityView, CommitSummaryView, WorkspacePeekView, WorkspaceStateView } from "@/lib/grove/types";

// The tabbed panel replacing the once-bare TerminalPane (#142). Pins: Terminal
// is the default tab and keeps every seam TerminalPane already owns; Diff
// re-homes the stat trio + diff lines + commit list; Info re-homes the
// metrics line + identity + timestamps; full-screen toggles `aria-pressed`.

function stateView(overrides: Partial<WorkspaceStateView> = {}): WorkspaceStateView {
  return {
    id: "w1",
    title: "Build the thing",
    repo_root: "/repo",
    branch: "grove/build-the-thing",
    base_branch: "main",
    worktree_path: "/repo/.worktrees/build",
    tmux_session: "grove-build",
    agent_name: "claude",
    status: "active",
    created_at: "2026-07-01T10:00:00Z",
    updated_at: "2026-07-01T10:00:00Z",
    branch_provenance: "grove",
    placement: "worktree",
    ...overrides,
  } as WorkspaceStateView;
}

function peek(overrides: Partial<WorkspacePeekView> = {}): WorkspacePeekView {
  return {
    state: stateView(),
    base_ahead: 2,
    base_behind: 0,
    diff_added: 120,
    diff_removed: 40,
    dirty_files: 3,
    recent_commits: [],
    agent_snapshot: "$ pytest\n12 passed",
    snapshot_taken_at: "2026-07-06T10:00:00Z",
    ...overrides,
  } as WorkspacePeekView;
}

const COMMITS: CommitSummaryView[] = [
  { sha: "abc1234def5678", subject: "feat: wire the panel", committed_at: "2026-07-01T09:00:00Z" },
];

const WORKING = AgentLiveStatus.of({
  state: "working",
  human_turns: 4,
  tool_calls: 9,
  tokens_in: 100,
  tokens_out: 200,
  active_subagents: 2,
  model: "claude-sonnet-4-5",
} as AgentActivityView);

describe("WorkPanel", () => {
  it("defaults to the Terminal tab, rendering the existing TerminalPane", () => {
    render(<WorkPanel peek={peek()} live={WORKING} commits={COMMITS} />);
    expect(screen.getByTestId("work-panel-tab-terminal")).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("terminal-pane")).toBeInTheDocument();
    expect(screen.getByTestId("peek-snapshot")).toHaveTextContent("pytest");
  });

  it("the Diff tab shows the stat trio, diff lines, and commit list", async () => {
    const user = userEvent.setup();
    render(<WorkPanel peek={peek()} live={WORKING} commits={COMMITS} />);
    await user.click(screen.getByTestId("work-panel-tab-diff"));
    expect(screen.getByTestId("stat-trio")).toBeInTheDocument();
    expect(screen.getByText(/\+120/)).toBeInTheDocument();
    expect(screen.getByTestId("commit-list")).toBeInTheDocument();
    expect(screen.queryByTestId("terminal-pane")).not.toBeInTheDocument();
  });

  it("the Info tab shows the metrics line, subagent count, agent+model, and placement", async () => {
    const user = userEvent.setup();
    render(
      <WorkPanel peek={peek({ state: stateView({ placement: "root" }) })} live={WORKING} commits={[]} />,
    );
    await user.click(screen.getByTestId("work-panel-tab-info"));
    expect(screen.getByTestId("metrics")).toHaveTextContent("4t");
    expect(screen.getByText(/2 bg agents/)).toBeInTheDocument();
    expect(screen.getByText("claude-sonnet-4-5")).toBeInTheDocument();
    expect(screen.getByTestId("placement-badge")).toBeInTheDocument();
  });

  it("the Info tab never renders the worktree path (host-private)", async () => {
    const user = userEvent.setup();
    render(<WorkPanel peek={peek()} live={WORKING} commits={[]} />);
    await user.click(screen.getByTestId("work-panel-tab-info"));
    expect(screen.queryByText(/\/repo\/\.worktrees/)).toBeNull();
  });

  it("full screen toggles aria-pressed", async () => {
    const user = userEvent.setup();
    render(<WorkPanel peek={peek()} live={WORKING} commits={[]} />);
    const toggle = screen.getByTestId("work-panel-fullscreen");
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-pressed", "true");
  });
});
