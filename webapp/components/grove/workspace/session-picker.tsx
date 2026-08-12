"use client";

import { Button } from "@/components/ui/button";
import { EmptyState, EmptyStateGreeting } from "@/components/elements/empty-state";
import type { SessionSummaryView } from "@/lib/grove/api";
import {
  useRemapSession,
  useWorkspaceSessionCandidates,
  useWorkspaceSessions,
} from "@/lib/grove/hooks";
import { sessionLabel } from "./selectors";

/**
 * The way out of a dead session pointer.
 *
 * A workspace can track a session id whose live successor the daemon's
 * adoption gate rejects; the attributed `/sessions` list then comes back empty
 * and the transcript has nothing to follow. The ungated candidate list is
 * fetched ONLY in that case — a healthy workspace must not pay for the extra
 * directory scan — and adopting a row IS the remap.
 *
 * The picker retires itself once a session resolves: a retained candidate list
 * must not resurrect a "track a session" control over a healthy workspace.
 */
export function SessionPicker({
  workspaceId,
  resolvedSessionId,
}: {
  workspaceId: string;
  resolvedSessionId: string | null;
}) {
  const stuck = resolvedSessionId === null;
  const sessions = useWorkspaceSessions(workspaceId);
  const candidates = useWorkspaceSessionCandidates(workspaceId, stuck);
  const remap = useRemapSession(workspaceId);

  if (!stuck) return null;

  const rows = pickRows(sessions.data, candidates.data);
  if (rows.length === 0) {
    return (
      <EmptyState data-testid="session-picker-empty">
        <EmptyStateGreeting>No agent session is attributed to this workspace yet.</EmptyStateGreeting>
      </EmptyState>
    );
  }

  return (
    <div className="flex flex-col gap-2 p-4" data-testid="session-picker">
      <p className="text-sm font-medium">Pick a session to follow</p>
      <ul className="flex flex-col gap-1">
        {rows.map((session) => (
          <li key={session.session_id} className="flex items-center gap-2">
            <span className="min-w-0 flex-1 truncate text-sm">{sessionLabel(session)}</span>
            <Button
              size="xs"
              variant="outline"
              disabled={remap.isPending}
              onClick={() => remap.mutate(session.session_id)}
              data-testid="session-picker-adopt"
            >
              Track
            </Button>
          </li>
        ))}
      </ul>
      {remap.error && (
        <p role="status" className="text-xs">
          {remap.error.message}
        </p>
      )}
    </div>
  );
}

/** Candidates only add value where the attributed list is empty; never show both. */
function pickRows(
  attributed: SessionSummaryView[] | undefined,
  candidates: SessionSummaryView[] | undefined,
): SessionSummaryView[] {
  return attributed && attributed.length > 0 ? attributed : (candidates ?? []);
}
