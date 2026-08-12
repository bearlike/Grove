import type { ExternalStoreThreadListAdapter } from "@assistant-ui/react";

import type { SessionSummaryView } from "@/lib/grove/api";

/**
 * A workspace's agent sessions as assistant-ui's thread list.
 *
 * Grove's "threads" are agent sessions the daemon discovered — they are never
 * created by the UI, so the adapter offers no new-thread, rename, archive or
 * delete verb. Switching is the whole interaction, and it is a pure view change:
 * pinning a session as the workspace's primary is a REMAP, a different and
 * destructive-ish action that stays an explicit control.
 */

/** Build the thread-list adapter for one workspace. Pure — the caller owns the
 * switch callback and the selected id. */
export function sessionThreadList(
  sessions: readonly SessionSummaryView[] | undefined,
  activeSessionId: string | null,
  onSwitchToThread: (sessionId: string) => void,
): ExternalStoreThreadListAdapter {
  return {
    ...(activeSessionId ? { threadId: activeSessionId } : {}),
    threads: (sessions ?? []).map((session) => ({
      status: "regular" as const,
      id: session.session_id,
      title: sessionTitle(session),
    })),
    onSwitchToThread,
  };
}

/**
 * A session's label, most specific fact first.
 *
 * A catalog-scoped row carries no `title` and no prompts, so falling through to
 * the bare session id is deliberate: rendering "untitled session" would hide
 * WHICH of several identical-looking rows you are looking at.
 */
export function sessionTitle(session: SessionSummaryView): string {
  return (
    session.title?.trim() ||
    session.workspace_title?.trim() ||
    session.first_prompt?.trim() ||
    session.workspace_branch?.trim() ||
    session.session_id
  );
}
