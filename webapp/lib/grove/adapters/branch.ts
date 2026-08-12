import type { WorkspaceStateView } from "@/lib/grove/api";

/**
 * Which git refs a workspace actually has, as opposed to which fields the wire
 * fills in.
 */

/**
 * The engine's literal string for "there is no branch here".
 *
 * It reaches the wire two ways, and both mean absence: a ROOT workspace — one
 * that adopts the repo's live checkout instead of creating a worktree — records
 * `base_branch = "HEAD"`, and `git.current_branch()` yields `"HEAD"` for a
 * detached checkout. Neither is a ref anyone can name, check out or push.
 */
const HEAD_SENTINEL = "HEAD";

/**
 * The branch this workspace forked FROM, or `null` when it has no separate base.
 *
 * The wire has no nullable base-branch field, so absence arrives as a sentinel
 * and every renderer that treats the field as a name prints a branch called
 * `HEAD` that does not exist. This is the one place that reading happens, so a
 * surface asks for the fact and writes its own sentence for the absence —
 * pushing the check into `BranchLabel` instead would make a shared atom rewrite
 * its own input, and a user may legitimately name a branch `HEAD-something`.
 *
 * The engine already names this exact field as the one that lies: `git log
 * <base>..<branch>` collapses to empty for precisely these workspaces however
 * much work has been done, which is why `base_commit` exists as a recorded
 * anchor. A base equal to the branch is that same absence said a second way —
 * a ref compared against itself answers nothing — so it returns `null` too.
 *
 * Structural rather than typed on `WorkspaceStateView`, because the fleet reads
 * its state out of the dashboard snapshot and the work panel reads it out of a
 * peek; the two are the same shape and neither should have to convert.
 */
export function baseBranchOf(state: Pick<WorkspaceStateView, "branch" | "base_branch">): string | null {
  const base = state.base_branch.trim();
  if (!base || base === HEAD_SENTINEL) return null;
  return base === state.branch.trim() ? null : base;
}
