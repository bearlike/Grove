import type { BranchProvenance, Placement, WorkspaceStatus } from "./types";

/**
 * Which lifecycle mutations are legal for a workspace, and what the kill flow's
 * delete-branch default is — the pure policy the webapp's action UI reads.
 *
 * This is a faithful client-side mirror of the engine's own gating (the TUI's
 * `_AVAILABLE_KEYS_BY_STATUS` + `_KEYS_REMOVED_BY_PLACEMENT`, and the manager's
 * `kill(delete_branch=None)` provenance default). It exists ONLY so the UI shows
 * the meaningful buttons and a smart kill default — the daemon is the real
 * gate: every action still rides the same typed-error path, so a stale snapshot
 * that offers an illegal action just surfaces the engine's refusal rather than
 * corrupting anything. Keep it a lookup table, never an `if status === …` chain.
 */
export type LifecycleAction = "pause" | "resume" | "respawn" | "kill";

// Status → the mutating lifecycle actions that apply. Mirrors the TUI footer
// gate, restricted to the create/pause/resume/respawn/kill verbs the webapp
// drives (attach/message/edit/sessions are separate surfaces). `running` is the
// raw-intent leak the reconciler usually hides, kept here for parity.
const ACTIONS_BY_STATUS: Record<WorkspaceStatus, readonly LifecycleAction[]> = {
  active: ["pause", "kill"],
  idle: ["pause", "kill"],
  running: ["pause", "kill"],
  paused: ["resume", "kill"],
  offline: ["respawn", "kill"],
  orphaned: ["kill"],
  error: ["kill"],
};

// Placement strips keys AFTER the status gate: a root workspace has no worktree,
// so the engine refuses pause/resume — offering them would be a trap.
const REMOVED_BY_PLACEMENT: Partial<Record<Placement, readonly LifecycleAction[]>> = {
  root: ["pause", "resume"],
};

/**
 * The lifecycle actions to offer for a workspace's current status + placement.
 * Falls back to `["kill"]` for an unknown status (kill is legal in every state)
 * rather than `undefined` — streamed enum values are untrusted at runtime, and
 * a neutral, always-safe default beats a crash (the render-hardening rule).
 */
export function availableActions(
  status: WorkspaceStatus,
  placement: Placement,
): LifecycleAction[] {
  const base = ACTIONS_BY_STATUS[status] ?? (["kill"] as const);
  const removed = REMOVED_BY_PLACEMENT[placement] ?? [];
  return base.filter((a) => !removed.includes(a));
}

/**
 * The kill flow's "delete the local branch" default checkbox value. Mirrors the
 * engine's `kill(delete_branch=None)` resolution: grove-created branches default
 * to deletion (Grove made them), user-attached branches are kept, and a root
 * workspace's branch is never Grove's to delete. The user can always override
 * the checkbox — this is just the safe starting point.
 */
export function defaultDeleteBranch(
  provenance: BranchProvenance,
  placement: Placement,
): boolean {
  if (placement === "root") return false;
  return provenance === "grove";
}
