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

/**
 * Is `id` present in the snapshot? The hook uses this to detect a
 * `session_activity` delta for a workspace it doesn't have yet.
 *
 * Why it matters (#49): the daemon's `poll_once` re-enumerates the store and
 * emits `session_activity` — *not* `workspace_changed` — for a workspace
 * created by a separate process (a second TUI, the MCP server, the CLI). The
 * bus-bridged `workspace_changed` lifecycle event only fires for ops that go
 * through the daemon's own in-process managers, so an out-of-band create never
 * triggers the hook's lifecycle re-fetch. `patchWorkspace` deliberately drops a
 * delta for an unknown workspace (keeps the wall stable), which would leave the
 * new row invisible until a reconnect/focus-heal. The hook closes that gap by
 * re-fetching the full snapshot when this predicate is false for an incoming
 * delta. Pure, so it stays a unit-test seam alongside the reducer.
 */
export function snapshotHasWorkspace(
  state: DashboardSnapshotView | null,
  id: string,
): boolean {
  if (!state) return false;
  return state.projects.some((g) => g.workspaces.some((w) => w.state.id === id));
}

// The daemon's connect-time snapshot and its poll loop race: a delta read
// BEFORE the snapshot was computed can be queued and delivered AFTER it.
// `observed_at` (when the engine actually read the row) is the true freshness
// key — the event seq is not. Malformed/missing timestamps fail open (apply
// the patch) so a bad clock can never freeze a card.
function isStaleDelta(
  existing: WorkspaceActivityView,
  incoming: WorkspaceActivityView,
): boolean {
  const have = Date.parse(existing.observed_at);
  const got = Date.parse(incoming.observed_at);
  return Number.isFinite(have) && Number.isFinite(got) && got < have;
}

/**
 * A cheap fingerprint of one session's transcript-progress signals, read
 * straight off the SSE-fed snapshot the session page already holds — no
 * second EventSource, no fetch, just a string the page diffs to know when to
 * invalidate the `["turns", …]` query (#166).
 *
 * Deliberately narrower than the session-rail's fingerprint (`state` alone,
 * `SessionRail`): a transcript can grow — a new tool call, a new assistant
 * reply — WITHOUT the coarse `state` changing (an agent can stay "working"
 * through several tool calls in a row), so `assistant_replies`/`tool_calls`/
 * `last_event_at` are the fields that actually correlate with turn progress.
 *
 * Returns `""` (a stable, comparable value) when the session isn't in the
 * snapshot yet — pre-connect, or a workspace/session the daemon hasn't
 * reported — so a caller's skip-first-run effect never fires on that gap.
 */
export function turnsProgressFingerprint(
  snapshot: DashboardSnapshotView | null,
  workspaceId: string,
  sessionId: string | null,
): string {
  if (!snapshot || !sessionId) return "";
  const ws = snapshot.projects
    .flatMap((g) => g.workspaces)
    .find((w) => w.state.id === workspaceId);
  const session = ws?.sessions.find((s) => s.session.session_id === sessionId);
  if (!session) return "";
  const { state, assistant_replies, tool_calls, last_event_at } = session.activity;
  return `${state}:${assistant_replies}:${tool_calls}:${last_event_at ?? ""}`;
}

function patchWorkspace(
  state: DashboardSnapshotView | null,
  changed: WorkspaceActivityView,
): DashboardSnapshotView | null {
  if (!state) return state;
  const current = state.projects
    .flatMap((g) => g.workspaces)
    .find((w) => w.state.id === changed.state.id);
  // An activity delta for a workspace we don't have yet (created since the last
  // snapshot) — leave state untouched; the lifecycle re-fetch will pick it up.
  if (!current) return state;
  if (isStaleDelta(current, changed)) return state;
  const projects: ProjectGroupView[] = state.projects.map((group) => ({
    ...group,
    workspaces: group.workspaces.map((existing) =>
      existing.state.id === changed.state.id ? changed : existing,
    ),
  }));
  const all = projects.flatMap((g) => g.workspaces);
  return {
    ...state,
    projects,
    total_workspaces: all.length,
    needs_attention: all.filter((w) => w.needs_attention).length,
  };
}
