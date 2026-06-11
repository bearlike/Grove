import type {
  AgentActivityState,
  DashboardSnapshotView,
  WorkspaceActivityView,
} from "@/lib/grove/types";

/**
 * Wire-shaped `WorkspaceActivityView` builder shared by the unit tests that
 * exercise the stream reducer and the wall-presentation policy. Attention is
 * derived from the agent state the same way the daemon does (waiting / blocked
 * / error want the human).
 */
export function workspace(
  id: string,
  state: AgentActivityState,
  observed_at = "2026-06-01T00:00:00Z",
): WorkspaceActivityView {
  const attention = state === "waiting" || state === "blocked" || state === "error";
  return {
    state: {
      id,
      title: `t-${id}`,
      repo_root: "/r",
      branch: "b",
      base_branch: "main",
      worktree_path: "/r/wt",
      tmux_session: `grove-${id}`,
      agent_name: "claude",
      status: "active",
      created_at: "2026-06-01T00:00:00Z",
      updated_at: "2026-06-01T00:00:00Z",
    } as WorkspaceActivityView["state"],
    recent_commits: [],
    observed_at,
    sessions: [
      {
        session: {
          session_id: `s-${id}`,
          adapter_kind: "claude_code",
          provenance: "grove_launched",
          tmux_window: "agent",
        },
        activity: {
          state,
          title: null,
          current_task: null,
          human_turns: 0,
          assistant_replies: 0,
          replies_per_turn: [],
          tool_calls: 0,
          model: null,
          tokens_in: 0,
          tokens_out: 0,
          last_event_at: null,
          needs_attention: attention,
          error_detail: null,
        },
      },
    ],
    base_ahead: 0,
    base_behind: 0,
    diff_added: 0,
    diff_removed: 0,
    dirty_files: 0,
    pane_target: null,
    needs_attention: attention,
  };
}

/** One-project snapshot wrapping the given workspaces, counts derived. */
export function snapshot(...ws: WorkspaceActivityView[]): DashboardSnapshotView {
  return {
    projects: [{ repo_root: "/r", repo_name: "r", workspaces: ws }],
    generated_at: "2026-06-01T00:00:00Z",
    total_workspaces: ws.length,
    needs_attention: ws.filter((w) => w.needs_attention).length,
  };
}
