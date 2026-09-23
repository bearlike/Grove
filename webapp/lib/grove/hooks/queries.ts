"use client";

import { useCallback, useEffect, useMemo, useRef } from "react";
import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";

import { GroveProtocolError } from "@/lib/grove/api";
import type { DiagramDocumentView, DiagramWriter } from "@/lib/grove/api";
import type { components } from "@/lib/grove/api/types.gen";
import type {
  AgentSummaryView,
  BranchInfo,
  CommitSummaryView,
  HealthView,
  MailboxDirectory,
  ModelOptionView,
  ProvisionProgressView,
  SessionControlsView,
  SessionDetailView,
  SessionSummaryView,
  SessionTurnView,
  TicketProviderView,
  TicketRef,
  TodoListView,
  WatchList,
  WhoamiView,
  WorkspaceActivityView,
  WorkspaceDiffView,
  WorkspaceHistoryView,
  WorkspacePanelView,
  WorkspacePeekView,
  WorkspaceQueueView,
  WorkspaceStateView,
} from "@/lib/grove/api";
import { commitsFingerprint, mergeTurns, queueFingerprint, turnCursor } from "@/lib/grove/adapters";
import { groveClient, POLL_MS } from "./client";
import { groveKeys } from "./keys";
import { backstopInterval, useActivityStream } from "./stream";

/**
 * Read hooks over the daemon.
 *
 * One hook per resource, and components never fetch directly — that is what
 * keeps cadence, key shape and error handling in one place. Every polled hook
 * documents its interval in `POLL_MS` rather than inline, so the whole request
 * budget is readable in one table.
 *
 * A `refetchInterval` here is a BACKSTOP and nothing else. `backstopInterval`
 * turns each one off while the event stream is connected, so a healthy surface
 * costs zero interval-driven requests and a dropped stream falls back to the
 * documented cadence. A hook whose data no event can refresh keeps its interval
 * unconditionally and says why at the call site — gating a surface the stream
 * does not cover would make it silently stale, which is worse than the poll.
 */

export function useWorkspace(id: string | null): UseQueryResult<WorkspaceStateView> {
  return useQuery({
    queryKey: groveKeys.workspace(id ?? ""),
    queryFn: () => groveClient.getWorkspace(id!),
    enabled: id !== null,
  });
}

/**
 * Working-tree stats plus the pane snapshot behind an open workspace.
 *
 * A `session_activity` frame is a strict superset of this route minus the pane
 * pair, and the stream writes those fields straight into this cache entry (see
 * `refreshPeekFromStream`), so a connected stream keeps the counters live at
 * zero requests. The pane pair has its own stream in `useWorkspacePane`.
 */
export function useWorkspacePeek(id: string | null): UseQueryResult<WorkspacePeekView> {
  const { connected } = useActivityStream();
  return useQuery({
    queryKey: groveKeys.peek(id ?? ""),
    queryFn: () => groveClient.getPeek(id!),
    enabled: id !== null,
    refetchInterval: backstopInterval(connected, POLL_MS.peek),
  });
}

/**
 * One workspace's activity row, fetched directly rather than found in the
 * fleet snapshot.
 *
 * This is what stops a workspace page waiting on the whole host. The snapshot
 * describes every workspace on the machine, so a page needing ONE session id
 * inherited the cost of workspaces it never renders — measured on the
 * reference host, two OFFLINE workspaces contributed 22.4 s of a 40.6 s
 * bootstrap that first paint sat behind.
 *
 * It carries the same `WorkspaceActivityView` the snapshot does, so the stream
 * remains the freshness mechanism and nothing downstream changes shape: once
 * `/events` delivers, its `session_activity` frames keep the row current and
 * this query is the backstop rather than the source of truth.
 */
export function useWorkspaceActivity(
  id: string | null,
): UseQueryResult<WorkspaceActivityView> {
  const { connected } = useActivityStream();
  return useQuery({
    queryKey: groveKeys.workspaceActivity(id ?? ""),
    queryFn: () => groveClient.getWorkspaceActivity(id!),
    enabled: id !== null,
    refetchInterval: backstopInterval(connected, POLL_MS.peek),
  });
}

/**
 * A workspace's steer queue — messages the harness is holding but has not yet
 * injected.
 *
 * The closest existing shape is `useWorkspaceCommits`. `useWorkspaceTodo`
 * below is now its true sibling in shape, cost and refusal semantics — both
 * are small fetch-on-demand routes bounded to one workspace. Same split as
 * commits: the ~1 Hz tick carries only the DEPTH
 * (`WorkspaceActivityView.queue.pending`), because the messages themselves are
 * unbounded in principle — so the edge invalidates on a depth change and the
 * interval is the backstop for a workspace nobody has open to diff against.
 *
 * Grove never keeps its own copy of this queue — seeding one optimistically
 * from Grove's own sends could not show a message typed straight into the pane,
 * which the harness queues too and Grove never saw. Every read here comes from
 * `GET /workspaces/{id}/queue`, never reconstructed.
 */
export function useWorkspaceQueue(id: string | null): UseQueryResult<WorkspaceQueueView> {
  const { connected, snapshot } = useActivityStream();
  useQueueEdge(id, queueFingerprint(snapshot, id ?? ""));
  return useQuery({
    queryKey: groveKeys.queue(id ?? ""),
    queryFn: () => groveClient.getQueue(id!),
    enabled: id !== null,
    refetchInterval: backstopInterval(connected, POLL_MS.queue),
  });
}

/**
 * The agent's current plan/checklist behind an open workspace.
 *
 * `/todo`'s sibling in shape, cost and refusal to `useWorkspaceQueue` above:
 * fetch-on-demand, bounded to one workspace, and a real distinction between
 * "no session at all" (404 `agent_session_not_found`, folded to `null` below)
 * and "a session that has called no todo tool yet" (a real, empty 200 —
 * `{items: []}`, passed through as-is). The two must never collapse into one
 * another; only `useGroveThread` decides whether an empty list itself renders
 * as "no plan".
 *
 * THIS HOOK EXISTS ONLY BECAUSE THE TRANSCRIPT IS NOW WINDOWED. The plan used
 * to be derived from `latestTodoFromTurns` scanning whatever turns were
 * loaded — correct only while the client held the WHOLE session from turn 0.
 * Once the first read became a tail (`useSessionTurns`'s `INITIAL_TURN_WINDOW`),
 * a plan the agent last wrote 200 turns back would silently vanish from the
 * card on open and only reappear once the agent wrote a new one. No frame on
 * `/events` carries the checklist itself — only bounded counts, on
 * `TodoProgressView` — so the interval is a real backstop, gated by the stream
 * exactly like the queue.
 */
export function useWorkspaceTodo(id: string | null): UseQueryResult<TodoListView | null> {
  const { connected } = useActivityStream();
  return useQuery({
    queryKey: groveKeys.todo(id ?? ""),
    queryFn: async () => {
      try {
        return await groveClient.getWorkspaceTodo(id!);
      } catch (error) {
        // A real, quiet "no plan" — not a fetch failure. Anything else
        // (network, 5xx, a different refusal code) is a genuine error and
        // must surface as one, not be swallowed alongside this.
        if (error instanceof GroveProtocolError && error.code === "agent_session_not_found") {
          return null;
        }
        throw error;
      }
    },
    enabled: id !== null,
    refetchInterval: backstopInterval(connected, POLL_MS.todo),
  });
}

/**
 * Every watch whose callback lands in this workspace — the card beside Queue.
 *
 * `useWorkspaceTodo`'s sibling in shape and cost: a small fetch-on-demand read
 * scoped to one workspace. Unlike the queue it has NO edge to invalidate on,
 * because no `/events` frame carries watch state — so the interval is ungated
 * and is the whole refresh. It only runs while a workspace page is open.
 */
export function useWorkspaceWatches(id: string | null): UseQueryResult<WatchList> {
  return useQuery({
    queryKey: groveKeys.watches(id ?? ""),
    queryFn: () => groveClient.getWorkspaceWatches(id!),
    enabled: id !== null,
    refetchInterval: POLL_MS.watches,
  });
}

/**
 * Every name, progress claim and ticket this workspace has recorded — the
 * durable history that survives the workspace being killed.
 *
 * **An empty view is the ordinary answer.** Recording is forward-only, so every
 * workspace created before the store shipped has nothing, and a caller must
 * render that as "nothing recorded yet" rather than as a broken panel.
 *
 * No `/events` frame carries these rows, so there is nothing to invalidate on;
 * the read is cheap (one indexed SQLite read). It gets no interval because the rows only grow when the agent reports a NEW claim, and
 * the claim it is reporting right now is already live on the Task card. The
 * dialog re-reads on open, which is the only moment a reader is looking.
 */
export function useWorkspaceHistory(
  id: string | null,
): UseQueryResult<WorkspaceHistoryView> {
  return useQuery({
    queryKey: groveKeys.history(id ?? ""),
    queryFn: () => groveClient.getWorkspaceHistory(id!),
    enabled: id !== null,
  });
}

/** The workspace's resolved embedded panels. */
export function useWorkspacePanels(
  id: string | null,
): UseQueryResult<WorkspacePanelView[]> {
  return useQuery({
    queryKey: groveKeys.panels(id ?? ""),
    queryFn: () => groveClient.getWorkspacePanels(id!),
    enabled: id !== null,
  });
}

/**
 * The workspace's diagram document.
 *
 * `watch` is the caller's statement that an EDITABLE diagram is on screen right
 * now — the tab is selected and the collaboration is active. Only then does
 * this poll, because the interval exists solely to notice a write that came
 * from outside this browser, and a diagram nobody is looking at has nobody to
 * warn. There is no global watcher and no fleet-wide cost: `WorkPanel` mounts
 * one tab at a time, so an unopened Diagram tab issues no request at all.
 *
 * Not gated on window focus. A reader watching an agent redraw a diagram on a
 * second screen is the case this is for, and `document.hidden` would call that
 * idle.
 */
export function useWorkspaceDiagram(
  id: string | null,
  repo: string,
  watch: boolean,
): UseQueryResult<DiagramDocumentView> {
  return useQuery({
    queryKey: groveKeys.diagram(id ?? ""),
    queryFn: ({ signal }) => groveClient.getDiagram(id!, repo, signal),
    enabled: id !== null,
    // UNGATED even though `workspace_source_changed` covers the edge, because
    // this interval is a CONFLICT DETECTOR rather than a freshness mechanism:
    // noticing that something wrote the `.drawio` under an open editor is what
    // turns a silent overwrite into a visible conflict, and a dropped stream
    // must not be the reason a draft is lost. Still mounted only by a visible,
    // editable diagram, so a stopped or unread one costs nothing.
    refetchInterval: watch ? POLL_MS.diagram : false,
  });
}

/**
 * The two diagram writes, as one stable object.
 *
 * A `DiagramWriter` rather than two `useMutation`s: the caller is a state
 * machine that serializes and coalesces its own saves (see
 * `runtime/diagram.ts`), and a mutation hook's retry, status and cache
 * behaviour would be a second, disagreeing opinion about the same queue. What
 * it does owe the cache is the acknowledged document, which every write returns
 * — so the query never has to re-fetch what the response already said.
 */
export function useDiagramWriter(id: string, repo: string): DiagramWriter {
  const queryClient = useQueryClient();
  return useMemo(() => {
    /**
     * Cancel the in-flight read BEFORE writing, and await the cancellation.
     *
     * A GET issued before this PUT would answer after it with the pre-write
     * bytes, and nothing downstream can distinguish that from somebody else
     * having written the file — so the client would raise a conflict against
     * its own save and block every later autosave. Cancelling is the only
     * seam that removes the ambiguity instead of guessing at it: the stale
     * answer is never delivered, so it is never observed.
     */
    const write = async (
      run: () => Promise<DiagramDocumentView>,
    ): Promise<DiagramDocumentView> => {
      await queryClient.cancelQueries({ queryKey: groveKeys.diagram(id) });
      const document = await run();
      // A poll may have started while the write was in flight. Fence that
      // read as well before publishing the acknowledged document to the cache.
      await queryClient.cancelQueries({ queryKey: groveKeys.diagram(id) });
      return record(document);
    };
    const record = (document: DiagramDocumentView) => {
      queryClient.setQueryData(groveKeys.diagram(id), document);
      // The descriptor's mode lives on the workspace record, so a stop has to
      // reach the surfaces reading THAT, not just this document.
      void queryClient.invalidateQueries({ queryKey: groveKeys.workspace(id) });
      return document;
    };
    return {
      update: (request) => write(() => groveClient.updateDiagram(id, repo, request)),
      stop: (request) => write(() => groveClient.stopDiagram(id, repo, request)),
    };
  }, [id, repo, queryClient]);
}

/**
 * The workspace's full branch log.
 *
 * The stream covers the EDGE but not the data: `recent_commits` on the activity
 * row is a three-row summary where this route is the uncapped
 * `git log base..branch`. So the interval is gated and the edge invalidates
 * instead — strictly fresher than the 15 s poll, and free while nothing lands.
 */
export function useWorkspaceCommits(id: string | null): UseQueryResult<CommitSummaryView[]> {
  const { connected, snapshot } = useActivityStream();
  useCommitEdge(id, commitsFingerprint(snapshot, id ?? ""));
  return useQuery({
    queryKey: groveKeys.commits(id ?? ""),
    queryFn: () => groveClient.getCommits(id!),
    enabled: id !== null,
    refetchInterval: backstopInterval(connected, POLL_MS.commits),
  });
}

/**
 * The worktree's uncommitted changes as one raw unified patch.
 *
 * GATED, and what made that possible is a new edge rather than a cheaper poll.
 * `dirty_files` moves only when the SET changes, so editing an already-dirty
 * file carried no activity delta and the interval was the only freshness there
 * was. `workspace_source_changed` is emitted per filesystem invalidation
 * regardless of whether any counter moved, so the edge now covers the case the
 * poll existed for. The interval stays as the stream-drop backstop, because the
 * patch bytes themselves are never in a frame.
 */
export function useWorkspaceDiff(id: string | null): UseQueryResult<WorkspaceDiffView> {
  const { connected } = useActivityStream();
  return useQuery({
    queryKey: groveKeys.diff(id ?? ""),
    queryFn: () => groveClient.getDiff(id!),
    enabled: id !== null,
    refetchInterval: backstopInterval(connected, POLL_MS.diff),
  });
}

/** The session's slash commands, skills, MCP servers and model catalog. */
export function useSessionControls(id: string | null): UseQueryResult<SessionControlsView> {
  return useQuery({
    queryKey: groveKeys.controls(id ?? ""),
    queryFn: () => groveClient.getControls(id!),
    enabled: id !== null,
  });
}

/**
 * The host's mailbox directory — who an `@` mention can name.
 *
 * Fetched on demand and never on the stream, like `controls`: no `/events`
 * frame carries it, and the composer only needs it when a reader types `@`.
 */
export function useMailboxContacts(): UseQueryResult<MailboxDirectory> {
  return useQuery({
    queryKey: groveKeys.mailboxContacts,
    queryFn: () => groveClient.getMailboxContacts(),
  });
}

type SharePolicyView = components["schemas"]["SharePolicyView"];

/** The public-share policy that every new link in a repository will inherit. */
export function useSharePolicy(repoRoot: string | null): UseQueryResult<SharePolicyView> {
  return useQuery({
    queryKey: groveKeys.sharePolicy(repoRoot ?? ""),
    queryFn: () => groveClient.getSharePolicy(repoRoot!),
    enabled: repoRoot !== null,
  });
}

/**
 * A container build's live log tail.
 *
 * `enabled` is the caller's status gate, not a default: a fleet that is not
 * building must cost zero requests, so this only ticks while a workspace is
 * actually provisioning.
 *
 * The log bytes stay off `/events`, but `session_activity` supplies the edge
 * that says provisioning changed, so the interval is a disconnected-stream
 * backstop and a live stream invalidates the active query immediately.
 */
export function useProvisionProgress(
  id: string | null,
  enabled: boolean,
): UseQueryResult<ProvisionProgressView> {
  const { connected } = useActivityStream();
  return useQuery({
    queryKey: groveKeys.provision(id ?? ""),
    queryFn: () => groveClient.getProvisionProgress(id!),
    enabled: id !== null && enabled,
    refetchInterval: backstopInterval(connected, POLL_MS.provision),
  });
}

/**
 * A workspace's attributed agent sessions, newest first.
 *
 * The stream carries the changed session set but not this route's richer
 * `SessionSummaryView` fields (`title`, prompts, `modified_at`, `size_bytes`).
 * Its `session_activity` edge therefore invalidates the richer read, and the
 * interval remains a disconnected-stream backstop.
 */
export function useWorkspaceSessions(
  id: string | null,
  limit?: number,
): UseQueryResult<SessionSummaryView[]> {
  const { connected } = useActivityStream();
  return useQuery({
    queryKey: groveKeys.sessions(id ?? ""),
    queryFn: () => groveClient.getSessions(id!, limit === undefined ? undefined : { limit }),
    enabled: id !== null,
    refetchInterval: backstopInterval(connected, POLL_MS.sessions),
  });
}

/**
 * The UNGATED session set for a workspace — the remap picker's source.
 *
 * The attributed list above is adoption-gated, so a workspace tracking a dead
 * session pointer gets an empty list and no way out. This one keeps exactly the
 * sessions that gate drops. Fetch it LAZILY (`enabled` only once the normal
 * resolution came back empty) — it costs an extra directory scan a healthy
 * workspace should never pay.
 */
export function useWorkspaceSessionCandidates(
  id: string | null,
  enabled: boolean,
): UseQueryResult<SessionSummaryView[]> {
  return useQuery({
    queryKey: groveKeys.sessionCandidates(id ?? ""),
    queryFn: () => groveClient.getSessions(id!, { candidates: true }),
    enabled: id !== null && enabled,
  });
}

/**
 * How many turns to request for the very FIRST read of a session, before
 * anything is held to derive a cursor from.
 *
 * A Grove "turn" is a human prompt plus the entire agent run that followed it
 * — every tool call, file edit and sub-agent thread, until the next human
 * prompt starts the next one — so a turn is nothing like a chat message.
 * Measured on the transcript that motivated this fix: 1,766 turns rendered as
 * ~25,900 DOM nodes, roughly 15 nodes per turn. Forty turns is therefore
 * already a substantial first paint (~600 nodes, comfortably more scrollback
 * than a reader takes in before the transcript below the fold even matters)
 * while staying two orders of magnitude short of downloading a long session's
 * full history. `loadEarlier` is what recovers the rest, on demand.
 */
export const INITIAL_TURN_WINDOW = 40;

/**
 * How many turns one "load earlier" click fetches.
 *
 * The same size as the first paint, and deliberately a CONSTANT page rather
 * than a doubling tail: each click now transfers only turns the reader does not
 * hold, so cost per click is flat and reading far back no longer re-downloads
 * everything below it.
 */
export const TURN_PAGE = INITIAL_TURN_WINDOW;

/** `useSessionTurns`'s return: the ordinary query state, plus the "load
 * earlier history" capability layered on top of it. */
export interface SessionTurnsQuery {
  /** The ordinary react-query state — `data`, `isPending`, `refetch`, … */
  query: UseQueryResult<SessionDetailView>;
  /** True once the held window's `first_turn_index` is above zero — there is
   * more history above what is currently rendered. */
  hasEarlier: boolean;
  /** Fetch one page of turns BEFORE the held window and prepend it. A no-op
   * while nothing is held yet, there is no earlier history, or a page is
   * already in flight. */
  loadEarlier: () => void;
  /** True while a `loadEarlier` fetch is in flight. */
  loadingEarlier: boolean;
}

/**
 * One session's transcript, read INCREMENTALLY once we hold any of it.
 *
 * Turns are never pushed over SSE — they are unbounded, so the daemon keeps the
 * stream small on purpose. `useTranscriptInvalidation` is what makes a live
 * transcript immediate, off the snapshot the page already holds; the interval
 * is the backstop for when there is no snapshot to diff.
 *
 * Every one of those triggers lands HERE, so making the fetch cursor-aware is
 * what turns all of them incremental at once — no second mechanism, no change
 * to the invalidation. Measured daemon-side: a progress tick that re-downloaded
 * 426 KB to gain 269 B costs 46 KB with the cursor.
 *
 * The FIRST read (nothing held yet) asks for a TAIL of `INITIAL_TURN_WINDOW`
 * turns rather than the whole session — this is what turns a 1,766-turn / 6.5
 * MB open into a bounded one. Every read after that follows via the cursor,
 * exactly as before; the daemon reports where the held window starts
 * (`first_turn_index`), which is what makes `hasEarlier` and `loadEarlier`
 * possible without a second request just to find out.
 *
 * An explicit `last` disables the cursor rather than racing it: they are
 * mutually exclusive on the wire, and a caller pinning a tail window has asked
 * for something other than "follow this session". It also bypasses the
 * `INITIAL_TURN_WINDOW` default, since the caller has already named a size.
 */
export function useSessionTurns(
  workspaceId: string | null,
  sessionId: string | null,
  last?: number,
): SessionTurnsQuery {
  const { connected } = useActivityStream();
  const queryClient = useQueryClient();
  const key = groveKeys.turns(workspaceId ?? "", sessionId ?? "");

  // No per-hook "how far have we widened" ref any more, and that is a property
  // of the backward page rather than a simplification: the next request is
  // derived entirely from the CACHE (`before_turn = held.first_turn_index`), so
  // the several `useSessionTurns` call sites behind one query key cannot
  // disagree about it, and a remount cannot reset it out of step with a window
  // the cache still holds.
  const query = useQuery({
    queryKey: key,
    queryFn: async () => {
      // The cache still holds the previous read while this one runs, which is
      // what makes the cursor derivable without a second copy of the transcript.
      const held = queryClient.getQueryData<SessionDetailView>(key);
      const afterTurn = last === undefined ? turnCursor(held) : undefined;
      // Three ways to land here: an explicit `last` (the caller pinned a tail
      // window), a cursor (following what we already hold), or — nothing held
      // and no cursor to follow — the first read, which asks for a TAIL rather
      // than the whole session. `held === undefined` is what distinguishes
      // that from the "held but empty" case just below it, which still means
      // "fetch whole" (no `last`, no cursor) exactly as before windowing.
      const options =
        last !== undefined ? { last }
        : afterTurn !== undefined ? { afterTurn }
        : held === undefined ? { last: INITIAL_TURN_WINDOW }
        : {};
      const window = await groveClient.getSessionTurns(workspaceId!, sessionId!, options);

      const merged = mergeTurns(held, window);
      if (merged.kind === "refetch") {
        // Self-healing and rare: the window could not be placed, so ask for the
        // session whole. One extra round trip beats a state machine, and beats
        // a transcript with a hole in it.
        return groveClient.getSessionTurns(workspaceId!, sessionId!);
      }
      // `unchanged` MUST reuse the held array, not an equal copy: `messages` is
      // memoised on that identity, so a fresh-but-equal array re-runs
      // `messagesFromTurns` over the whole transcript for a transcript that did
      // not change. Note this only covers the genuinely-empty window — a tick
      // that carries the tail still rebuilds every message, because
      // `messagesFromTurns` keys nothing per turn. That cost is unchanged by
      // the cursor and is a separate fix.
      return merged.kind === "unchanged"
        ? { ...window, turns: held!.turns, first_turn_index: held!.first_turn_index }
        : { ...window, turns: merged.turns as SessionTurnView[], first_turn_index: merged.first_turn_index };
    },
    enabled: workspaceId !== null && sessionId !== null,
    refetchInterval: backstopInterval(connected, POLL_MS.turns),
  });

  // `loadEarlier` is a one-off imperative fetch, not a `last` the query hook
  // itself carries — once ANY window is held, `queryFn` above always follows
  // via the cursor, so reaching further back has to happen outside that loop
  // and write the result straight into the cache. Every observer of this query
  // key (the steerable pane AND the read gate in `transcript.tsx`) then
  // re-renders off the same write; no second mechanism.
  //
  // It fetches a BACKWARD PAGE (`before_turn=<first held index>&last=40`), not
  // a doubled tail. The old widen re-downloaded everything the reader already
  // had to gain one page in front of it — measured on a real session as
  // `last=40` 3.71 MB then `last=80` 4.61 MB, i.e. ~3.7 MB re-transferred for
  // ~0.9 MB of new turns. A page carries only what is missing, so the cost of
  // reading further back is flat rather than quadratic in how far you go.
  const widen = useMutation({
    mutationFn: (): Promise<SessionDetailView> => {
      const held = queryClient.getQueryData<SessionDetailView>(key);
      return groveClient.getSessionTurns(workspaceId!, sessionId!, {
        beforeTurn: held?.first_turn_index ?? 0,
        last: TURN_PAGE,
      });
    },
    onSuccess: (window) => {
      const held = queryClient.getQueryData<SessionDetailView>(key);
      const merged = mergeTurns(held, window, "backward");
      // `refetch` means the page did not end where the held window starts —
      // a poll landed between the request and its answer, so the honest move
      // is to let the ordinary query re-read rather than splice a guess.
      if (merged.kind === "refetch") {
        void queryClient.invalidateQueries({ queryKey: key });
        return;
      }
      if (merged.kind === "replace") {
        queryClient.setQueryData<SessionDetailView>(key, {
          ...window,
          turns: merged.turns as SessionTurnView[],
          first_turn_index: merged.first_turn_index,
        });
      }
    },
  });

  const loadEarlier = useCallback(() => {
    if (workspaceId === null || sessionId === null || widen.isPending) return;
    const held = queryClient.getQueryData<SessionDetailView>(key);
    if (!held || held.first_turn_index <= 0) return;
    widen.mutate();
  }, [workspaceId, sessionId, widen, queryClient, key]);

  return {
    query,
    hasEarlier: (query.data?.first_turn_index ?? 0) > 0,
    loadEarlier,
    loadingEarlier: widen.isPending,
  };
}

/**
 * Every agent session in the catalog.
 *
 * Catalog source watching publishes `catalog_changed` for every relevant file
 * mutation, so this list refreshes from that edge and polls only while the main
 * stream is unavailable.
 */
export function useSessionCatalog(limit?: number): UseQueryResult<SessionSummaryView[]> {
  const { connected } = useActivityStream();
  return useQuery({
    queryKey: groveKeys.catalog(limit),
    queryFn: () => groveClient.getSessionCatalog(limit),
    refetchInterval: backstopInterval(connected, POLL_MS.catalog),
  });
}

/**
 * One catalog session's transcript, read-only.
 *
 * Never polled: an archived transcript does not grow under you. The `cwd` must
 * round-trip byte-for-byte from the row that produced it — the daemon matches a
 * RECORDED cwd by string, so a normalized path is a guaranteed miss.
 */
export function useCatalogTurns(
  sessionId: string | null,
  kind: string | null,
  cwd: string | null,
  last?: number,
): UseQueryResult<SessionDetailView> {
  return useQuery({
    queryKey: groveKeys.catalogTurns(sessionId ?? "", kind ?? "", cwd ?? ""),
    queryFn: () => groveClient.getCatalogTurns(sessionId!, kind!, cwd!, last),
    enabled: sessionId !== null && kind !== null && cwd !== null,
  });
}

/** The agents a repo may launch, each with the daemon-resolved model catalog. */
export function useAgents(repo: string | null): UseQueryResult<AgentSummaryView[]> {
  return useQuery({
    queryKey: groveKeys.agents(repo ?? ""),
    queryFn: () => groveClient.listAgents(repo!),
    enabled: repo !== null,
  });
}

/**
 * One agent's models, each with the name and context window a picker draws.
 *
 * Separate from `useAgents` because the enrichment is per agent and the agent
 * pill changes under the model pill: a catalog keyed only by repo would show
 * the previous agent's models after a switch. No `refetchInterval` — a model
 * catalog changes when config or a gateway does, neither of which the activity
 * stream reports, so polling it would be a timer nothing ever answers.
 */
export function useModels(
  repo: string | null,
  agent: string | null,
): UseQueryResult<ModelOptionView[]> {
  return useQuery({
    queryKey: groveKeys.models(repo ?? "", agent ?? ""),
    queryFn: () => groveClient.listModels(repo!, agent),
    enabled: repo !== null,
  });
}

export function useBranches(
  repo: string | null,
  scope: "local" | "remote",
): UseQueryResult<BranchInfo[]> {
  return useQuery({
    queryKey: groveKeys.branches(repo ?? "", scope),
    queryFn: () => groveClient.listBranches(repo!, scope),
    enabled: repo !== null,
  });
}

export function useHealth(): UseQueryResult<HealthView> {
  return useQuery({ queryKey: groveKeys.health, queryFn: () => groveClient.getHealth() });
}

export function useWhoami(): UseQueryResult<WhoamiView> {
  return useQuery({ queryKey: groveKeys.whoami, queryFn: () => groveClient.getWhoami() });
}

/**
 * The trackers this repo is wired to, and whether each one actually has
 * credentials.
 *
 * NO INTERVAL, and this is the one place it needs saying: provider *config* is
 * a file on disk that changes when a human edits it, so polling it would be a
 * request per surface per tick to learn nothing. `configured` is what tells a
 * client a provider is enabled but cannot answer — the difference between "no
 * such tracker" and "this tracker has no token", which are different sentences
 * to put in front of a user.
 */
export function useTicketProviders(repo: string | null): UseQueryResult<TicketProviderView[]> {
  return useQuery({
    queryKey: groveKeys.ticketProviders(repo ?? ""),
    queryFn: () => groveClient.listTicketProviders(repo!),
    enabled: repo !== null,
    staleTime: TICKET_STALE_MS,
  });
}

/** Live ticket reads, merged back onto the stored refs by the caller. */
export type TicketResolutions = {
  /** `ticketKey(ref)` → the tracker's current answer, absent until one arrives. */
  readonly byKey: ReadonlyMap<string, TicketRef>;
  /** A resolve is in flight for a ref that has no answer yet. */
  readonly loading: boolean;
  /** How many refs asked and were refused — never a reason to blank a row. */
  readonly failed: number;
  /** Ask the failed ones again; the one action an error state can offer. */
  readonly retry: () => void;
};

/**
 * Resolve stored refs against their trackers — N requests for N tickets, on
 * purpose.
 *
 * The stored `TicketRef` already carries a title and a status, cached at attach
 * time, so this is enrichment over data that is ALWAYS there: a workspace opened
 * three weeks after its issue was closed would otherwise show `open` forever.
 * It is cheap because N is the number of tickets on ONE workspace (one or two
 * in practice) and the card only mounts on the Info tab.
 *
 * `retry: false` because a tracker that 404s or has no token will 404 again;
 * the row degrades to its cached fields and the surface says so. No interval:
 * nothing on `/events` describes a third-party tracker, and polling somebody
 * else's forge once per open workspace is a cost no user asked for — the stale
 * window is `TICKET_STALE_MS` and a remount re-reads.
 *
 * `configuredProviders` gates the request rather than filtering the list, so a
 * ref whose provider has no credentials costs zero doomed round trips and the
 * caller still knows the ref exists.
 */
export function useTickets(
  repo: string | null,
  refs: readonly TicketRef[],
  configuredProviders: readonly TicketRef["provider"][],
): TicketResolutions {
  const resolvable = new Set(configuredProviders);
  return useQueries({
    queries: refs.map((ref) => ({
      queryKey: groveKeys.ticket(repo ?? "", ref.provider, ref.id),
      queryFn: () => groveClient.getTicket(repo!, ref.provider, ref.id),
      enabled: repo !== null && resolvable.has(ref.provider),
      staleTime: TICKET_STALE_MS,
      retry: false,
    })),
    combine: (results) => ({
      byKey: new Map(
        results.flatMap((result, index) => {
          const ref = refs[index];
          return result.data && ref ? ([[ticketKey(ref), result.data]] as const) : [];
        }),
      ),
      // `isLoading`, not `isPending`: a disabled query is pending forever, so
      // pending would report an unconfigured provider as permanently loading.
      loading: results.some((result) => result.isLoading),
      failed: results.filter((result) => result.isError).length,
      retry: () => {
        for (const result of results) if (result.isError) void result.refetch();
      },
    }),
  });
}

/** The coordinate a tracker links on. Exported so a caller can key its own rows. */
export function ticketKey(ref: Pick<TicketRef, "provider" | "kind" | "id">): string {
  return `${ref.provider}:${ref.kind}:${ref.id}`;
}

/**
 * How long a tracker's answer stays fresh. Minutes, not seconds: an issue's
 * state changes on human time, and this is the only knob standing between an
 * open Info tab and a request to somebody else's forge.
 */
const TICKET_STALE_MS = 5 * 60_000;

/**
 * Re-read the commit log when the stream says the log moved.
 *
 * Same shape as `useTranscriptInvalidation` and for the same reason: the event
 * proves something changed but does not carry the answer. Skips its first run,
 * so mounting never doubles the query's own first fetch.
 */
function useCommitEdge(workspaceId: string | null, fingerprint: string): void {
  const queryClient = useQueryClient();
  const previous = useRef<string | null>(null);

  useEffect(() => {
    if (previous.current !== null && previous.current !== fingerprint && workspaceId) {
      void queryClient.invalidateQueries({ queryKey: groveKeys.commits(workspaceId) });
    }
    previous.current = fingerprint;
  }, [fingerprint, workspaceId, queryClient]);
}

/**
 * Re-read the steer queue when the stream says its DEPTH moved.
 *
 * Same shape as `useCommitEdge`: the `message_sent` lifecycle event already
 * covers Grove's own sends (see `stream.tsx`'s `queue_changed` handling), so
 * this is what catches everything else that can move the count — most
 * importantly a message typed straight into the pane, which fires no lifecycle
 * event at all and is only ever visible as the depth moving on the next tick.
 */
function useQueueEdge(workspaceId: string | null, fingerprint: string): void {
  const queryClient = useQueryClient();
  const previous = useRef<string | null>(null);

  useEffect(() => {
    if (previous.current !== null && previous.current !== fingerprint && workspaceId) {
      void queryClient.invalidateQueries({ queryKey: groveKeys.queue(workspaceId) });
    }
    previous.current = fingerprint;
  }, [fingerprint, workspaceId, queryClient]);
}
