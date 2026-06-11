import type {
  DashboardEvent,
  DashboardSnapshotView,
  ProjectGroupView,
  WorkspaceActivityView,
} from "./types";

// The stream contract: how SSE `DashboardEvent`s fold into one snapshot. Pure
// reducer only — wall-presentation policy (filtering, sorting, lenses) lives in
// `dashboard-filter.ts` so transport changes never touch presentation.

/**
 * Reduce one SSE `DashboardEvent` into the dashboard snapshot state.
 *
 * Pure — the seam the `useActivityStream` hook (and its tests) lean on. The
 * stream contract:
 *   - `snapshot`         → replace the whole state (sent on connect / on a
 *                          too-stale reconnect that couldn't replay).
 *   - `session_activity` → patch the one changed workspace in place, keeping the
 *                          rest of the wall stable (no full re-fetch flicker).
 *   - everything else (`workspace_changed`, `heartbeat`, `pane_snapshot`) →
 *     no state change here; the hook handles the lifecycle re-fetch separately.
 */
export function applyDashboardEvent(
  state: DashboardSnapshotView | null,
  event: DashboardEvent,
): DashboardSnapshotView | null {
  if (event.kind === "snapshot") {
    return event.snapshot ?? state;
  }
  if (event.kind === "session_activity" && event.workspace) {
    return patchWorkspace(state, event.workspace);
  }
  return state;
}

function patchWorkspace(
  state: DashboardSnapshotView | null,
  changed: WorkspaceActivityView,
): DashboardSnapshotView | null {
  if (!state) return state;
  let found = false;
  const projects: ProjectGroupView[] = state.projects.map((group) => ({
    ...group,
    workspaces: group.workspaces.map((existing) => {
      if (existing.state.id === changed.state.id) {
        found = true;
        return changed;
      }
      return existing;
    }),
  }));
  // An activity delta for a workspace we don't have yet (created since the last
  // snapshot) — leave state untouched; the lifecycle re-fetch will pick it up.
  if (!found) return state;
  const all = projects.flatMap((g) => g.workspaces);
  return {
    ...state,
    projects,
    total_workspaces: all.length,
    needs_attention: all.filter((w) => w.needs_attention).length,
  };
}
