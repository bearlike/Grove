import type { DashboardSnapshotView, WorkspaceStateView } from "@/lib/grove/api";

/**
 * The fleet's vocabulary, derived from the wire types rather than restated.
 *
 * `lib/grove/api` re-exports the two roots (`DashboardSnapshotView`,
 * `WorkspaceStateView`) but not the shapes nested inside them. Indexing into
 * the roots keeps every literal union here identical to the daemon's by
 * construction — a status the daemon adds becomes a type error at the mapping
 * tables in `tokens.ts` instead of silently falling through to a default.
 */
export type ProjectGroup = DashboardSnapshotView["projects"][number];
export type WorkspaceActivity = ProjectGroup["workspaces"][number];
export type Phase = NonNullable<WorkspaceActivity["phase"]>;
export type TodoProgress = NonNullable<WorkspaceActivity["todo"]>;

/** Grove's three status axes, in the order a reader scans them. */
export type WorkspaceStatus = WorkspaceStateView["status"];
export type AgentState = WorkspaceActivity["sessions"][number]["activity"]["state"];
export type TaskPhase = Phase["phase"];

export type Runtime = WorkspaceStateView["runtime"];

/** One workspace lifted out of its project, so the fleet can be searched flat. */
export interface FleetRow {
  readonly workspace: WorkspaceActivity;
  readonly repoName: string;
  readonly repoRoot: string;
}
