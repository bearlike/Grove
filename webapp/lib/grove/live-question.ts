import type { AgentQuestionView, DashboardSnapshotView } from "./types";

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
 * malformed/differently-shaped snapshot (e.g. a stubbed-fetch test response
 * never meant to model activity) and against a stale `answered:true` entry
 * slipping through — both degrade to "nothing pending" rather than throwing
 * (render-hardening, same rule `chat-turns.ts` follows for an unrecognized
 * digest role). Returns `[]` for "nothing pending", never `null` — callers
 * gate on `.length > 0` and pass the group straight to `PendingQuestionCard`.
 */
export function livePendingQuestions(
  snapshot: DashboardSnapshotView | null | undefined,
  workspaceId: string,
  sessionId: string | null,
): AgentQuestionView[] {
  if (!snapshot || !sessionId || !Array.isArray(snapshot.projects)) return [];
  for (const group of snapshot.projects) {
    for (const ws of group.workspaces) {
      if (ws.state.id !== workspaceId) continue;
      const sa = ws.sessions.find((s) => s.session.session_id === sessionId);
      const questions = sa?.activity?.questions;
      return Array.isArray(questions) ? questions.filter((q) => !q.answered) : [];
    }
  }
  return [];
}
