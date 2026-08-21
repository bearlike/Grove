import type {
  AgentQuestionView,
  DashboardEvent,
  DashboardSnapshotView,
  WorkspaceActivityView,
  WorkspacePeekView,
  WorkspaceStateView,
} from "@/lib/grove/api";

/**
 * The dashboard event stream, folded purely.
 *
 * The transport (an `EventSource`, its reconnect and its staleness self-heal)
 * lives in `hooks/`; this module is only the fold and the lookups over the
 * folded snapshot, so the whole freshness contract is testable without a socket.
 */

/**
 * Fold one stream event into the snapshot.
 *
 * Returns the SAME reference when nothing changed, so a consumer can use
 * identity to skip a render. A delta for a workspace the snapshot has never
 * seen is deliberately dropped here rather than grafted on: a partial row would
 * render as a workspace with no identity. The transport promotes that case to a
 * full re-fetch, because it is how an out-of-band create (a second TUI, the MCP
 * server) first becomes visible.
 */
export function applyDashboardEvent(
  snapshot: DashboardSnapshotView | null,
  event: DashboardEvent,
): DashboardSnapshotView | null {
  if (event.kind === "snapshot") return event.snapshot ?? snapshot;
  if (event.kind !== "session_activity" || !event.workspace || !snapshot) return snapshot;

  const incoming = event.workspace;
  let changed = false;
  const projects = snapshot.projects.map((project) => {
    const index = project.workspaces.findIndex((w) => w.state.id === incoming.state.id);
    if (index === -1) return project;
    // `observed_at` is the freshness key, never the event seq: the daemon's
    // connect-time snapshot and its poll loop race, so a delta READ before the
    // snapshot was computed can still arrive after it and would roll a row back.
    // A malformed timestamp fails open — a bad clock must not freeze a card.
    if (!isFresher(incoming, project.workspaces[index]!)) return project;
    changed = true;
    const workspaces = [...project.workspaces];
    workspaces[index] = incoming;
    return { ...project, workspaces };
  });

  if (!changed) return snapshot;
  return {
    ...snapshot,
    projects,
    needs_attention: projects.reduce(
      (total, project) =>
        total + project.workspaces.filter((workspace) => workspace.needs_attention).length,
      0,
    ),
  };
}

/** Every workspace in the snapshot, flattened out of its project grouping. */
export function allWorkspaces(
  snapshot: DashboardSnapshotView | null,
): WorkspaceActivityView[] {
  return snapshot?.projects.flatMap((project) => project.workspaces) ?? [];
}

/** One workspace's live row, or null when the snapshot has not seen it. */
export function findWorkspaceActivity(
  snapshot: DashboardSnapshotView | null,
  workspaceId: string,
): WorkspaceActivityView | null {
  return allWorkspaces(snapshot).find((w) => w.state.id === workspaceId) ?? null;
}

/**
 * When this workspace last DID anything, as the original timestamp string.
 *
 * `state.updated_at` is not that instant and must never be shown as though it
 * were. It is the WORKSPACE RECORD's write time, and the record is only
 * written when Grove itself changes it — a lifecycle verb, a status
 * transition, a ticket attach. An agent that has been working continuously for
 * four hours touches none of those, so the field sits frozen at whatever Grove
 * last wrote while the session underneath it is seconds old. Measured on this
 * host on 2026-08-11: a live, actively-working workspace whose record said
 * `updated_at` was 4h 07m old.
 *
 * The agent's own `last_event_at` is the truest signal; the record's stamps
 * back it up so a brand-new, session-less workspace still reports an age
 * instead of nothing.
 *
 * Returns the STRING, not a parsed instant, because callers both display and
 * sort by this — and those must be the same value, or a list prints ages that
 * disagree with the order they are printed in.
 */
export function lastActivityIso(workspace: WorkspaceActivityView): string | null {
  return newestActivityIso(
    workspace.sessions,
    workspace.state.updated_at,
    workspace.state.created_at,
  );
}

/**
 * The same rule as {@link lastActivityIso}, with the record's stamps passed in
 * rather than read off an embedded workspace state.
 *
 * It exists because the public share view carries the session rows and the
 * workspace identity as two separate objects — its activity payload embeds no
 * `WorkspaceStateView`, deliberately, since that shape carries host paths a
 * public reader may not see. Rather than let that surface derive recency its own
 * way (and disagree with every other one), the fallback chain is the parameter
 * and the rule stays here, in one place.
 *
 * Fallbacks are tried in the order given, newest wins overall.
 */
export function newestActivityIso(
  sessions: readonly { activity: { last_event_at?: string | null } }[],
  ...fallbacks: readonly (string | null | undefined)[]
): string | null {
  const stamps: readonly (string | null | undefined)[] = [
    ...sessions.map((session) => session.activity.last_event_at),
    ...fallbacks,
  ];

  let newest = 0;
  let newestIso: string | null = null;
  for (const stamp of stamps) {
    if (!stamp) continue;
    const at = Date.parse(stamp);
    // A malformed timestamp fails open — one bad clock must not sink a row.
    if (Number.isFinite(at) && at > newest) {
      newest = at;
      newestIso = stamp;
    }
  }
  return newestIso;
}

/** The same instant as {@link lastActivityIso}, in epoch milliseconds, for sorting. */
export function lastActivityAt(workspace: WorkspaceActivityView): number {
  const iso = lastActivityIso(workspace);
  return iso === null ? 0 : Date.parse(iso);
}

/**
 * The session a workspace's chrome should follow by default.
 *
 * The engine always places the workspace's own pinned session FIRST in the
 * activity row, so this is the only source that survives a remap — the plain
 * `/sessions` list sorts by modification time and would silently un-pin a
 * freshly remapped session on the next load.
 */
export function primarySessionId(
  snapshot: DashboardSnapshotView | null,
  workspaceId: string,
): string | null {
  return findWorkspaceActivity(snapshot, workspaceId)?.sessions[0]?.session.session_id ?? null;
}

/**
 * The questions a session is blocked on right now.
 *
 * These arrive on the stream rather than in the transcript because the agent
 * flushes NOTHING while a question is on screen — `/turns` structurally cannot
 * see a pending ask.
 */
export function pendingQuestions(
  snapshot: DashboardSnapshotView | null,
  workspaceId: string,
  sessionId: string | null,
): AgentQuestionView[] {
  if (!sessionId) return [];
  const sessions = findWorkspaceActivity(snapshot, workspaceId)?.sessions ?? [];
  const match = sessions.find((entry) => entry.session.session_id === sessionId);
  return match?.activity.questions.filter((question) => !question.answered) ?? [];
}

/**
 * A fingerprint of one session's TURN progress, for invalidating an open
 * transcript off the snapshot the page already holds — no second stream.
 *
 * Narrower than the fleet's own row fingerprint on purpose: an agent stays
 * `working` across several tool calls, so a state-only key misses real turn
 * growth and the transcript would only refresh on the poll backstop. Returns
 * `""` for a session the snapshot has not seen, which is a stable value, so a
 * skip-first-run guard cannot misfire on that gap.
 */
export function turnsProgressFingerprint(
  snapshot: DashboardSnapshotView | null,
  workspaceId: string,
  sessionId: string | null,
): string {
  if (!sessionId) return "";
  const sessions = findWorkspaceActivity(snapshot, workspaceId)?.sessions ?? [];
  const match = sessions.find((entry) => entry.session.session_id === sessionId);
  if (!match) return "";
  const { state, assistant_replies, tool_calls, last_event_at } = match.activity;
  return `${state}:${assistant_replies}:${tool_calls}:${last_event_at ?? ""}`;
}

/**
 * What the transport should DO with one frame.
 *
 * The routing is here rather than in the effect that performs it because the
 * interesting decision is "can this frame be applied, or must we re-read the
 * world" — and that is a question about the wire shape, testable with no
 * socket and no query client.
 */
export type StreamAction =
  /** The frame IS the state; adopt it wholesale. */
  | { kind: "adopt"; snapshot: DashboardSnapshotView }
  /** The frame carries a complete row; fold it in and refresh that workspace. */
  | { kind: "apply"; snapshot: DashboardSnapshotView; workspace: WorkspaceActivityView }
  /** The workspace is gone. The snapshot is already computed — just store it,
   * and evict whatever else was cached under that id. */
  | { kind: "drop"; snapshot: DashboardSnapshotView; workspaceId: string }
  /** Lifecycle metadata moved that no delta carries; re-read ONE workspace. */
  | { kind: "resync"; workspaceId: string }
  /** The steer queue's DEPTH moved; re-read `GET /workspaces/{id}/queue`. */
  | { kind: "queue_changed"; workspaceId: string }
  /** The frame says only that something moved; re-read `/activity`. */
  | { kind: "refetch" }
  /** Nothing to do — a keepalive, or a frame for another stream. */
  | { kind: "ignore" };

/**
 * What a `workspace_changed` frame's `detail.event` means for the cache.
 *
 * Read explicitly from `detail.event` and never inferred from the frame's
 * SHAPE: a `killed` that were deduced from "no payload" would silently start
 * resurrecting dead workspaces the day the daemon attaches one, and this is the
 * one frame kind that can express a deletion at all — `poll_once` signals a
 * vanished workspace by ceasing to emit for it, which no delta can say.
 *
 * `updated` is the only verb carrying state no delta replaces: title,
 * description and `ticket_refs` are absent from the activity fingerprint, so
 * nothing else would ever bring them over. Most other verbs move `status`,
 * which IS in the fingerprint, so the next `session_activity` (≤2 s) carries
 * the whole row for free — and most steering acks move nothing at all.
 *
 * `message_sent` is the one steering ack that IS an exception: it can push the
 * text onto the harness's queue, which the activity fingerprint never carries
 * (the tick only holds `QueueDepthView.pending`, never the messages). Routing
 * it to `queue_changed` rather than `ignore` is what makes a just-sent message
 * appear without waiting for the depth to move on its own next tick.
 *
 * An unlisted verb falls through to a full re-read. Being conservative there
 * costs one request; being clever costs correctness.
 */
const LIFECYCLE_EVENTS: Readonly<Record<string, "drop" | "resync" | "queue" | "ignore">> = {
  killed: "drop",
  updated: "resync",
  created: "ignore",
  provisioning: "ignore",
  error: "ignore",
  paused: "ignore",
  resumed: "ignore",
  respawned: "ignore",
  offline_detected: "ignore",
  orphaned_detected: "ignore",
  control_invoked: "ignore",
  message_sent: "queue",
  question_answered: "ignore",
};

/** Remove a workspace from the snapshot, keeping the derived counts honest. */
export function dropWorkspace(
  snapshot: DashboardSnapshotView,
  workspaceId: string,
): DashboardSnapshotView | null {
  if (!findWorkspaceActivity(snapshot, workspaceId)) return null;
  const projects = snapshot.projects.map((project) => ({
    ...project,
    workspaces: project.workspaces.filter((w) => w.state.id !== workspaceId),
  }));
  return {
    ...snapshot,
    projects,
    total_workspaces: projects.reduce((n, p) => n + p.workspaces.length, 0),
    needs_attention: projects.reduce(
      (n, p) => n + p.workspaces.filter((w) => w.needs_attention).length,
      0,
    ),
  };
}

/**
 * Graft a freshly-read `WorkspaceStateView` onto the row that already holds it.
 *
 * The activity row's `state` is exactly this shape, so a targeted read replaces
 * that member and leaves the live session/counter half alone — which is the
 * point, since the delta stream owns those and a one-workspace read does not.
 */
export function applyWorkspaceState(
  snapshot: DashboardSnapshotView | null,
  state: WorkspaceStateView,
): DashboardSnapshotView | null {
  if (!snapshot || !findWorkspaceActivity(snapshot, state.id)) return null;
  return {
    ...snapshot,
    projects: snapshot.projects.map((project) => ({
      ...project,
      workspaces: project.workspaces.map((w) => (w.state.id === state.id ? { ...w, state } : w)),
    })),
  };
}

/**
 * Route one frame, purely.
 *
 * Only `workspace_changed` should ever reach `refetch` on a healthy fleet: the
 * daemon builds it from the manager bus with an id and a detail map and NOTHING
 * else (`activity.py::_bridge_callback`), so it genuinely carries no delta. A
 * `session_activity` carries the whole `WorkspaceActivityView` and is applied —
 * unless the snapshot has never seen that workspace, which is an out-of-band
 * create (a second TUI, the MCP server, another CLI) whose only path into the
 * fleet is that same re-read.
 *
 * IF THE DAEMON EVER PUTS A ROW ON `workspace_changed`, this is where it lands
 * — one more clause below, not a rewrite. It must NOT simply widen the kind
 * check, though: that frame also announces a kill, and applying a row for a
 * killed workspace re-seats the thing the frame said was gone. Such a clause
 * has to read `detail.event` and keep the refetch for the removing verbs.
 */
export function streamAction(
  snapshot: DashboardSnapshotView | null,
  event: DashboardEvent,
): StreamAction {
  if (event.kind === "heartbeat" || event.kind === "pane_snapshot") return { kind: "ignore" };
  if (event.kind === "snapshot") {
    return event.snapshot ? { kind: "adopt", snapshot: event.snapshot } : { kind: "ignore" };
  }
  if (
    event.kind === "session_activity" &&
    event.workspace &&
    snapshot &&
    findWorkspaceActivity(snapshot, event.workspace.state.id)
  ) {
    const folded = applyDashboardEvent(snapshot, event);
    // The fold returns the SAME reference when it changed nothing, which is how
    // it refuses a delta that was read before the row we already hold. Acting
    // on that would roll the peek counters back off a stale row — the exact
    // regression `isFresher` exists to prevent, reintroduced one layer up.
    return folded && folded !== snapshot
      ? { kind: "apply", snapshot: folded, workspace: event.workspace }
      : { kind: "ignore" };
  }

  if (event.kind === "workspace_changed" && snapshot && event.workspace_id) {
    switch (LIFECYCLE_EVENTS[event.detail.event ?? ""]) {
      case "drop": {
        const dropped = dropWorkspace(snapshot, event.workspace_id);
        // Already absent — a second `killed`, or a workspace this client never
        // saw. Nothing to remove and nothing to re-read.
        return dropped
          ? { kind: "drop", snapshot: dropped, workspaceId: event.workspace_id }
          : { kind: "ignore" };
      }
      case "resync":
        return { kind: "resync", workspaceId: event.workspace_id };
      case "queue":
        return { kind: "queue_changed", workspaceId: event.workspace_id };
      case "ignore":
        return { kind: "ignore" };
    }
  }

  return { kind: "refetch" };
}

/**
 * A streamed activity row, projected onto the peek shape.
 *
 * A `session_activity` frame is a strict superset of `GET /workspaces/{id}/peek`
 * minus the pane — the daemon runs the SAME four git reads on both sides — so a
 * peek already in the cache is refreshable from the stream rather than
 * re-polled. The pane pair is the one exception, and it is carried over from
 * `previous` rather than nulled: it is the pre-first-frame fallback for the
 * pane's own separate stream, and no event on `/events` can refresh it.
 */
export function peekFromActivity(
  activity: WorkspaceActivityView,
  previous: WorkspacePeekView,
): WorkspacePeekView {
  return {
    state: activity.state,
    base_ahead: activity.base_ahead,
    base_behind: activity.base_behind,
    diff_added: activity.diff_added,
    diff_removed: activity.diff_removed,
    dirty_files: activity.dirty_files,
    recent_commits: activity.recent_commits,
    agent_snapshot: previous.agent_snapshot,
    snapshot_taken_at: previous.snapshot_taken_at,
  };
}

/**
 * A fingerprint of everything that can change one workspace's commit LIST.
 *
 * `GET /workspaces/{id}/commits` is the uncapped `git log base..branch`, where
 * the activity row carries only a three-row summary — so the stream covers the
 * EDGE but not the data, and this is what turns the edge into an invalidation.
 * The head sha alone is not enough: a rebase or a fetch moves `base` without
 * touching the tip, and both change the log. Returns `""` for a workspace the
 * snapshot has not seen, which is stable, so a skip-first-run guard cannot
 * misfire on that gap.
 */
export function commitsFingerprint(
  snapshot: DashboardSnapshotView | null,
  workspaceId: string,
): string {
  const row = findWorkspaceActivity(snapshot, workspaceId);
  if (!row) return "";
  return `${row.recent_commits[0]?.sha ?? ""}:${row.base_ahead}:${row.base_behind}`;
}

/**
 * A fingerprint of one workspace's steer-queue DEPTH, for invalidating the
 * message list off the snapshot the page already holds.
 *
 * Same split as `commitsFingerprint`: the ~1 Hz tick carries only
 * `QueueDepthView.pending`, never the messages, so this is what turns a moved
 * count into an invalidation of `GET /workspaces/{id}/queue`. This is also the
 * mechanism that makes the queue card BIDIRECTIONAL — a message typed straight
 * into the pane, which Grove never sent, still moves the harness's own depth
 * counter on the very next tick, and this fingerprint changing is what notices.
 * Returns `""` for a workspace the snapshot has not seen, which is stable, so a
 * skip-first-run guard cannot misfire on that gap.
 */
export function queueFingerprint(
  snapshot: DashboardSnapshotView | null,
  workspaceId: string,
): string {
  const row = findWorkspaceActivity(snapshot, workspaceId);
  if (!row) return "";
  return row.queue ? String(row.queue.pending) : "none";
}

function isFresher(incoming: WorkspaceActivityView, current: WorkspaceActivityView): boolean {
  const next = Date.parse(incoming.observed_at);
  const previous = Date.parse(current.observed_at);
  if (Number.isNaN(next) || Number.isNaN(previous)) return true;
  return next >= previous;
}
