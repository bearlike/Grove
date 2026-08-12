import type { DashboardSnapshotView } from "@/lib/grove/api";

import type { AgentState, ProjectGroup, WorkspaceActivity } from "@/components/grove/fleet/types";

/**
 * Wire-shaped fleet fixtures.
 *
 * Typed as the generated wire views, not as hand-written literals with a cast:
 * `codegen:check` keeps `types.gen.ts` honest against the daemon's OpenAPI, so
 * a field the daemon renames breaks these builders at TYPECHECK time. A cast
 * would sail past that and the fixtures would rot silently.
 */

type WorkspaceState = WorkspaceActivity["state"];
type SessionActivity = WorkspaceActivity["sessions"][number];

export interface WorkspaceSpec {
  readonly id: string;
  readonly title?: string;
  readonly agentName?: string;
  readonly branch?: string;
  readonly needsAttention?: boolean;
  readonly state?: AgentState;
  /** The agent's last event. `null` means the agent has never reported one. */
  readonly lastEventAt?: string | null;
  readonly updatedAt?: string;
  readonly createdAt?: string;
  /** `false` builds a workspace with no session at all. */
  readonly hasSession?: boolean;
}

function workspaceState(spec: WorkspaceSpec): WorkspaceState {
  return {
    id: spec.id,
    title: spec.title ?? spec.id,
    repo_root: "/repos/grove",
    branch: spec.branch ?? `feat/${spec.id}`,
    base_branch: "main",
    worktree_path: `/repos/grove-worktrees/${spec.id}`,
    tmux_session: `grove-${spec.id}`,
    agent_name: spec.agentName ?? "claude",
    status: "active",
    created_at: spec.createdAt ?? "2026-08-01T00:00:00Z",
    updated_at: spec.updatedAt ?? spec.createdAt ?? "2026-08-01T00:00:00Z",
    branch_provenance: "grove",
    placement: "worktree",
    ticket_refs: [],
    runtime: "host",
    runtime_default_config: false,
    runtime_no_tmux: false,
  };
}

function session(spec: WorkspaceSpec): SessionActivity {
  return {
    session: {
      session_id: `session-${spec.id}`,
      adapter_kind: "claude",
      provenance: "grove_launched",
      tmux_window: null,
    },
    activity: {
      state: spec.state ?? "idle",
      title: null,
      current_task: null,
      human_turns: 1,
      assistant_replies: 1,
      replies_per_turn: [1],
      tool_calls: 0,
      active_subagents: 0,
      model: null,
      tokens_in: 0,
      tokens_out: 0,
      last_event_at: spec.lastEventAt ?? null,
      needs_attention: spec.needsAttention ?? false,
      error_detail: null,
      questions: [],
    },
  };
}

export function workspace(spec: WorkspaceSpec): WorkspaceActivity {
  return {
    state: workspaceState(spec),
    sessions: spec.hasSession === false ? [] : [session(spec)],
    base_ahead: 0,
    base_behind: 0,
    diff_added: 0,
    diff_removed: 0,
    dirty_files: 0,
    pane_target: null,
    needs_attention: spec.needsAttention ?? false,
    recent_commits: [],
    observed_at: "2026-08-11T00:00:00Z",
  };
}

export function project(
  repoName: string,
  repoRoot: string,
  workspaces: readonly WorkspaceActivity[],
): ProjectGroup {
  return {
    repo_root: repoRoot,
    repo_name: repoName,
    cwd: repoRoot,
    workspaces: [...workspaces],
    error: null,
  };
}

export function snapshot(projects: readonly ProjectGroup[]): DashboardSnapshotView {
  const total = projects.reduce((count, group) => count + group.workspaces.length, 0);
  const attention = projects.reduce(
    (count, group) => count + group.workspaces.filter((w) => w.needs_attention).length,
    0,
  );
  return {
    projects: [...projects],
    total_workspaces: total,
    needs_attention: attention,
    generated_at: "2026-08-11T00:00:00Z",
  };
}
