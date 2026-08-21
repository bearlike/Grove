"use client";

import { useCallback, useEffect, useRef } from "react";
import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";

import { GroveProtocolError } from "@/lib/grove/api";
import type { components } from "@/lib/grove/api/types.gen";
import type {
  AgentSummaryView,
  BranchInfo,
  CommitSummaryView,
  HealthView,
  ProvisionProgressView,
  SessionControlsView,
  SessionDetailView,
  SessionSummaryView,
  SessionTurnView,
  TicketProviderView,
  TicketRef,
  TodoListView,
  WhoamiView,
  WorkspaceDiffView,
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
 * UNGATED, and the exception proves the rule. The stream carries `dirty_files`,
 * which is the same SET this route diffs — but editing a file that is already
 * dirty moves no counter, so a fingerprint edge would leave the tab confidently
 * stale on the most common change there is. No frame carries patch content, so
 * the interval is the only freshness there is.
 *
 * It is also the largest payload the app fetches — 133 KB on a 33-file tree,
 * bounded at 1 MB by the daemon — which is why the interval is the slowest in
 * the table and why this hook is only ever mounted by the Files tab. `WorkPanel`
 * mounts one tab at a time, so a workspace nobody is reading a diff for costs
 * nothing at all.
 */
export function useWorkspaceDiff(id: string | null): UseQueryResult<WorkspaceDiffView> {
  return useQuery({
    queryKey: groveKeys.diff(id ?? ""),
    queryFn: () => groveClient.getDiff(id!),
    enabled: id !== null,
    refetchInterval: POLL_MS.diff,
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
 * The interval is deliberately UNGATED. No frame on `/events` carries the
 * provision log — the daemon reads it per request off the on-disk file — so
 * gating this on the stream would freeze a build's only progress display for
 * the whole build window, which is the surface it exists to fix.
 */
export function useProvisionProgress(
  id: string | null,
  enabled: boolean,
): UseQueryResult<ProvisionProgressView> {
  return useQuery({
    queryKey: groveKeys.provision(id ?? ""),
    queryFn: () => groveClient.getProvisionProgress(id!),
    enabled: id !== null && enabled,
    refetchInterval: POLL_MS.provision,
  });
}

/**
 * A workspace's attributed agent sessions, newest first.
 *
 * The interval is deliberately UNGATED. The stream's `sessions` array carries
 * `AgentSessionView`, which is a different and much thinner shape than this
 * route's `SessionSummaryView` — `title`, `first_prompt`, `last_prompt`,
 * `modified_at` and `size_bytes` ride no event at all, and they are exactly
 * what the session picker and the thread list render.
 */
export function useWorkspaceSessions(
  id: string | null,
  limit?: number,
): UseQueryResult<SessionSummaryView[]> {
  return useQuery({
    queryKey: groveKeys.sessions(id ?? ""),
    queryFn: () => groveClient.getSessions(id!, limit === undefined ? undefined : { limit }),
    enabled: id !== null,
    refetchInterval: POLL_MS.sessions,
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

/** `useSessionTurns`'s return: the ordinary query state, plus the "load
 * earlier history" capability layered on top of it. */
export interface SessionTurnsQuery {
  /** The ordinary react-query state — `data`, `isPending`, `refetch`, … */
  query: UseQueryResult<SessionDetailView>;
  /** True once the held window's `first_turn_index` is above zero — there is
   * more history above what is currently rendered. */
  hasEarlier: boolean;
  /** Double the requested tail and merge the newly-arrived earlier turns onto
   * what is held. A no-op while nothing is held yet, there is no earlier
   * history, or a widen is already in flight. */
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

  // The tail size `loadEarlier` will next ask for, doubling on every call.
  // Reset whenever the session changes — an in-render comparison rather than
  // an effect, so a widened window from the PREVIOUS session can never leak
  // into the first paint of a freshly opened one.
  //
  // `useSessionTurns` is called TWICE for one session — once here (via
  // `useGroveThread`) and once more in `transcript.tsx`'s own three-state
  // gate — so there are two independent `requestedRef`s and two `widen`
  // mutations behind the SAME query key. Harmless today because only
  // `useGroveThread`'s `loadEarlier` is ever exposed and called; if a second
  // call site ever starts calling ITS `loadEarlier` too, the two refs would
  // disagree about the next size to request and this stops being harmless.
  const sessionRef = useRef(sessionId);
  const requestedRef = useRef(INITIAL_TURN_WINDOW);
  if (sessionRef.current !== sessionId) {
    sessionRef.current = sessionId;
    requestedRef.current = INITIAL_TURN_WINDOW;
  }

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
  // via the cursor, so widening the requested size has to happen outside that
  // loop and write the wider result straight into the cache. Every observer
  // of this query key (the steerable pane AND the read gate in
  // `transcript.tsx`) then re-renders off the same write; no second mechanism.
  const widen = useMutation({
    // The size is computed here but only COMMITTED to `requestedRef` in
    // `onSuccess`, below — a failed fetch must leave the next click asking for
    // the same doubling again, not skip a size. Doubling eagerly (before the
    // request) would make a transient failure permanently forget how far the
    // reader had widened.
    mutationFn: (): Promise<SessionDetailView> =>
      groveClient.getSessionTurns(workspaceId!, sessionId!, { last: requestedRef.current * 2 }),
    onSuccess: (window) => {
      requestedRef.current *= 2;
      const held = queryClient.getQueryData<SessionDetailView>(key);
      const merged = mergeTurns(held, window);
      // A `last`-only fetch is never incremental, so `mergeTurns` always takes
      // the wholesale-replace branch here — `unchanged`/`refetch` cannot occur.
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
 * Every agent session on the host — a browse surface, deliberately off the
 * activity tick.
 *
 * The interval is deliberately UNGATED. This is the host-wide catalog, most of
 * whose rows belong to sessions Grove never launched and no workspace owns, so
 * nothing on `/events` describes them.
 */
export function useSessionCatalog(limit?: number): UseQueryResult<SessionSummaryView[]> {
  return useQuery({
    queryKey: groveKeys.catalog(limit),
    queryFn: () => groveClient.getSessionCatalog(limit),
    refetchInterval: POLL_MS.catalog,
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
