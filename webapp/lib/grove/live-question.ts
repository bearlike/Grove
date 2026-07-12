import type { AgentQuestionView, DashboardSnapshotView, WorkspaceActivityView } from "./types";

/**
 * Find one workspace's activity entry in a (possibly stale/malformed)
 * snapshot. Shared traversal for every per-workspace lookup below — defensive
 * against a snapshot shape a stubbed-fetch test never meant to model activity
 * (render-hardening: degrade to "not found", never throw).
 */
export function findWorkspaceActivity(
  snapshot: DashboardSnapshotView | null | undefined,
  workspaceId: string,
): WorkspaceActivityView | null {
  if (!snapshot || !Array.isArray(snapshot.projects)) return null;
  for (const group of snapshot.projects) {
    for (const ws of group.workspaces) {
      if (ws.state.id === workspaceId) return ws;
    }
  }
  return null;
}

/**
 * Find the live pending question GROUP for one workspace's session, read
 * straight off the SSE snapshot (`useActivityStream`) rather than the
 * fetch-on-demand `/turns` poll. `AskUserQuestion`'s `tool_use` never reaches
 * the transcript until it's answered (research-findings.md), so `/turns`
 * cannot see it while pending — the activity stream is fed by the ask-time
 * hook instead and carries the payload live. `questions` is a LIST, ordered
 * as asked — one `AskUserQuestion` call can batch N questions (the
 * "Color / Toppings" tab-bar case) answered atomically in one POST, so the
 * wire exposes the whole pending group. Defensive against a
 * malformed/differently-shaped snapshot and against a stale `answered:true`
 * entry slipping through — both degrade to "nothing pending" rather than
 * throwing. Returns `[]` for "nothing pending", never `null` — callers
 * gate on `.length > 0` and pass the group straight to `PendingQuestionCard`.
 */
export function livePendingQuestions(
  snapshot: DashboardSnapshotView | null | undefined,
  workspaceId: string,
  sessionId: string | null,
): AgentQuestionView[] {
  if (!sessionId) return [];
  const ws = findWorkspaceActivity(snapshot, workspaceId);
  const sa = ws?.sessions.find((s) => s.session.session_id === sessionId);
  const questions = sa?.activity?.questions;
  return Array.isArray(questions) ? questions.filter((q) => !q.answered) : [];
}

/**
 * The daemon's tracked PRIMARY session id for one workspace (issue #121),
 * read straight off the activity snapshot. The engine's `sessions_for()`
 * always places `state.agent_session_id` (the minted-or-remapped pointer)
 * first when it resolves live — `WorkspaceActivityView.sessions[0]`, the same
 * source `displayState()` (`dashboard-filter.ts`) reads for the card's status.
 * This is NOT the same ordering as `GET /workspaces/{id}/sessions`
 * (`SessionSummaryView[]`, what `useWorkspaceSessions` returns): that endpoint
 * sorts purely by `modified_at` and carries no "is primary" concept at all —
 * `WorkspaceStateView` deliberately doesn't expose `agent_session_id` either
 * (views-never-expose). So this is the ONE signal that reflects a
 * `useRemapSession` pin durably (including across a fresh page load, once the
 * daemon's `session_remapped` event lands and the snapshot refetches) — a
 * caller defaulting to `sessions[0]` off the plain list alone would silently
 * revert to the most-recently-modified session instead of the pinned one.
 * Returns `null` when the workspace has no session yet or the snapshot hasn't
 * resolved — callers fall back to their own default (e.g. `sessions[0]`).
 */
export function primarySessionId(
  snapshot: DashboardSnapshotView | null | undefined,
  workspaceId: string,
): string | null {
  const ws = findWorkspaceActivity(snapshot, workspaceId);
  return ws?.sessions[0]?.session.session_id ?? null;
}
