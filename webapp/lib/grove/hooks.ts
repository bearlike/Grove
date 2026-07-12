"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { GroveClient } from "./client";
import { applyDashboardEvent, snapshotHasWorkspace } from "./activity-stream";
import type { QuestionAnswerItem } from "./question-plan";
import type {
  AgentSummaryView,
  BranchInfo,
  CommitSummaryView,
  CreateWorkspaceRequest,
  DashboardEvent,
  DashboardSnapshotView,
  SessionControlsView,
  SessionDetailView,
  SessionSummaryView,
  WhoamiView,
  WorkspacePaneView,
  WorkspaceStateView,
  WorkspacePeekView,
} from "./types";

const client = GroveClient.default();

export function useWorkspaces() {
  return useQuery<WorkspaceStateView[]>({
    queryKey: ["workspaces"],
    queryFn: () => client.listWorkspaces(),
    refetchInterval: 5_000,
  });
}

/**
 * Daemon identity + uptime for the persistent footer. Refreshes every
 * 30 s — uptime is slow-changing relative to peek/workspaces, so a
 * hotter cadence would just burn the daemon for no UI gain. The
 * StatusBar interpolates seconds locally between fetches via a tick
 * effect so the displayed "up Xm Ys" still ticks live.
 */
export function useDaemonWhoami() {
  return useQuery<WhoamiView>({
    queryKey: ["whoami"],
    queryFn: () => client.getWhoami(),
    refetchInterval: 30_000,
  });
}

export function useWorkspace(id: string) {
  return useQuery<WorkspaceStateView>({
    queryKey: ["workspace", id],
    queryFn: () => client.getWorkspace(id),
    refetchInterval: 5_000,
    enabled: Boolean(id),
  });
}

export function useWorkspacePeek(id: string) {
  return useQuery<WorkspacePeekView>({
    queryKey: ["peek", id],
    queryFn: () => client.getPeek(id),
    refetchInterval: 2_000,
    enabled: Boolean(id),
  });
}

/**
 * Comprehensive branch history for the detail page. Daemon's
 * `GET /workspaces/{id}/commits` returns every commit on the workspace
 * branch since fork from base — the detail page renders the full list,
 * the TUI rail keeps using `peek.recent_commits`. Cadence is slower
 * (15 s) than peek because commit history changes much less often than
 * the agent pane.
 */
export function useWorkspaceCommits(id: string) {
  return useQuery<CommitSummaryView[]>({
    queryKey: ["commits", id],
    queryFn: () => client.getCommits(id),
    refetchInterval: 15_000,
    enabled: Boolean(id),
  });
}

/**
 * The session's available input controls (#178) — the work panel's Controls
 * tab reader. Slash commands / skills / MCP servers / model catalog change
 * rarely (a config edit, a model switch), so the poll is a slow backstop; the
 * tab mounts this only when opened (conditional render), so idle tabs cost
 * nothing. A `switchModel` mutation invalidates this key to refresh
 * `current_model` promptly.
 */
export function useSessionControls(id: string) {
  return useQuery<SessionControlsView>({
    queryKey: ["controls", id],
    queryFn: () => client.getControls(id),
    refetchInterval: 30_000,
    enabled: Boolean(id),
  });
}

/**
 * Recorded agent sessions for the detail page's Sessions panel. History-tier
 * cadence (15 s, same as commits): a session list changes when an agent run
 * starts/ends or a transcript grows — minutes apart, not seconds.
 */
export function useWorkspaceSessions(id: string) {
  return useQuery<SessionSummaryView[]>({
    queryKey: ["sessions", id],
    queryFn: () => client.getSessions(id),
    refetchInterval: 15_000,
    enabled: Boolean(id),
  });
}

/**
 * The UNGATED, cwd-scoped candidate sessions for a workspace — `GET
 * /workspaces/{id}/sessions?candidates=true` (#132). Where `useWorkspaceSessions`
 * returns the daemon's own ATTRIBUTED history (adoption-gated), this KEEPS the
 * sessions the gate drops — a dead-minted-pointer's live successor, a foreign
 * session sharing a ROOT cwd — so a remap picker can offer the session the
 * workspace should actually follow when its tracked pointer is dead.
 *
 * Fetched LAZILY (caller passes `enabled`): it is an extra directory parse, so
 * the page turns it on ONLY when the workspace has no usable tracked session —
 * never on the healthy path. Its key is a child of the gated list's, so a
 * `useRemapSession` invalidation (`["sessions", id]`, prefix-matched) refreshes
 * both. Same 15 s history cadence as the gated list.
 */
export function useWorkspaceSessionCandidates(id: string, { enabled }: { enabled: boolean }) {
  return useQuery<SessionSummaryView[]>({
    queryKey: ["sessions", id, "candidates"],
    queryFn: () => client.getSessions(id, { candidates: true }),
    refetchInterval: 15_000,
    enabled: Boolean(id) && enabled,
  });
}

/**
 * Every recorded agent session across one project's worktrees — the home
 * page's per-repo Sessions section. Fetched on expand only: the section
 * mounts its body (and therefore this hook) when the user opens it, and
 * `repo` null keeps the query disabled (zero requests), exactly the
 * `useSessionTurns` gating. History-tier cadence (15 s, same as commits)
 * while expanded — session lists change when runs start/end, minutes apart.
 */
export function useProjectSessions(repo: string | null) {
  return useQuery<SessionSummaryView[]>({
    queryKey: ["project-sessions", repo],
    queryFn: () => client.getProjectSessions(repo as string),
    refetchInterval: 15_000,
    enabled: Boolean(repo),
  });
}

/**
 * The ADE session rail's batched reader (#140): every project's sessions at
 * once, keyed identically to `useProjectSessions` so the two share one cache
 * entry per repo (a rail refetch warms a later single-repo read and vice-versa).
 * `useQueries` is the one idiomatic way to fan a dynamic-length list of repos
 * into N parallel queries under the Rules of Hooks — a per-repo child component
 * would re-mount its query on every reorder and couldn't feed the rail's
 * cross-project attention group. Same 15 s history cadence; `combine` folds the
 * results into a `repo → sessions` map plus an aggregate loading flag so the
 * rail reads one value. The 15 s poll is the freshness backstop; the rail layers
 * an SSE "refresh now" invalidation on top (see `SessionRail`).
 */
export function useProjectSessionsAll(repos: string[]) {
  return useQueries({
    queries: repos.map((repo) => ({
      queryKey: ["project-sessions", repo],
      queryFn: () => client.getProjectSessions(repo),
      refetchInterval: 15_000,
      enabled: Boolean(repo),
    })),
    combine: (results) => ({
      byRepo: new Map(repos.map((repo, i) => [repo, results[i]?.data ?? []])),
      isLoading: results.some((r) => r.isLoading),
    }),
  });
}

/**
 * One session's conversation digest, fetched on expand only (`sessionId` null →
 * disabled, zero requests). The digest is a transcript read, the heaviest
 * per-request endpoint here, so `refetchMs` is a BACKSTOP, not the freshness
 * mechanism (#166): the session page layers an SSE-driven invalidation on top,
 * firing `queryClient.invalidateQueries(["turns", …])` the instant
 * `turnsProgressFingerprint` (`activity-stream.ts`) shows the session's
 * `assistant_replies`/`tool_calls`/`last_event_at` actually advanced — so a
 * real turn lands immediately instead of waiting out the poll. The chat panel
 * still passes a hotter `refetchMs` than the default (it's a conversation
 * surface, mounted only while in view) as the floor under that invalidation —
 * cadence is the caller's policy, the hook is the mechanism.
 */
export function useSessionTurns(id: string, sessionId: string | null, refetchMs = 30_000) {
  return useQuery<SessionDetailView>({
    queryKey: ["turns", id, sessionId],
    queryFn: () => client.getSessionTurns(id, sessionId as string, 100),
    refetchInterval: refetchMs,
    enabled: Boolean(id) && Boolean(sessionId),
  });
}

/**
 * Steer the workspace's agent with a follow-up message — POST
 * `/workspaces/{id}/message`, 204 on success. The one optimistic write in the
 * app: `onMutate` appends a pending user turn to the session's turns cache so
 * the sent message appears instantly; the next transcript fetch is the
 * reconciliation (it replaces the whole array, so no rollback bookkeeping —
 * on error we just invalidate to drop the phantom row early). `sessionId`
 * names the cache target; null (no recorded session yet) sends without the
 * optimistic echo.
 */
export function useSendMessage(workspaceId: string, sessionId: string | null) {
  const queryClient = useQueryClient();
  const turnsKey = ["turns", workspaceId, sessionId];
  return useMutation({
    mutationFn: (text: string) => client.sendMessage(workspaceId, text),
    onMutate: async (text: string) => {
      if (!sessionId) return;
      await queryClient.cancelQueries({ queryKey: turnsKey });
      queryClient.setQueryData<SessionDetailView>(turnsKey, (prev) =>
        prev
          ? {
              ...prev,
              turns: [
                ...prev.turns,
                { user_text: text, started_at: new Date().toISOString(), entries: [] },
              ],
            }
          : prev,
      );
    },
    onError: () => {
      // The send never reached the agent — refetch so the optimistic row drops.
      if (sessionId) void queryClient.invalidateQueries({ queryKey: turnsKey });
    },
  });
}

/**
 * Interrupt the workspace's agent — POST `/workspaces/{id}/interrupt`, 204 on
 * success. Callers gate the affordance on the agent actually WORKING (mirror
 * of the dashboard's live-toggle gating); a 409/501 refusal envelope is the
 * daemon saying "nothing to interrupt / adapter can't" and renders as a quiet
 * notice, never a crash.
 */
export function useInterrupt(workspaceId: string) {
  return useMutation({
    mutationFn: () => client.interrupt(workspaceId),
  });
}

/**
 * Invoke a named session control — a slash command or a skill (#178). Mirrors
 * `useSendMessage`/`useInterrupt`: dispatch-only, no optimistic cache write (the
 * result rides the transcript stream, not a refetch here). A 501
 * `capability_unavailable` / 409 refusal surfaces as a quiet inline notice.
 */
export function useInvokeControl(workspaceId: string) {
  return useMutation({
    mutationFn: (name: string) => client.invokeControl(workspaceId, name),
  });
}

/**
 * Switch the running session's model (#178). On success, invalidate the
 * controls key so `current_model` refreshes (its poll backstop is slow); the
 * actual switch takes effect on the agent's side and rides the transcript.
 */
export function useSwitchModel(workspaceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (model: string) => client.switchModel(workspaceId, model),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["controls", workspaceId] });
    },
  });
}

/**
 * Answer a live pending `AskUserQuestion` — POST
 * `/workspaces/{id}/question-answer`, 204 dispatched. Mirrors
 * `useSendMessage`/`useInterrupt`: no optimistic cache write, because there is
 * no query this hook owns — the question's resolution rides the SSE-carried
 * `questions` list emptying (`useActivityStream`), not a refetch here. The
 * caller wires the 409/422 refusal to an inline notice and degrades the card
 * to read-only pending (root CLAUDE.md: side effects at the edges).
 */
export function useAnswerQuestion(workspaceId: string) {
  return useMutation({
    mutationFn: ({
      sessionId,
      toolUseId,
      answers,
    }: {
      sessionId: string;
      toolUseId: string;
      answers: QuestionAnswerItem[];
    }) => client.answerQuestion(workspaceId, sessionId, toolUseId, answers),
  });
}

/**
 * Pin an existing session as the workspace's tracked primary (#121) — POST
 * `/workspaces/{id}/session`, `mutate(sessionId)`. `SessionSummaryView`
 * carries no "is primary" flag and `useWorkspaceSessions` sorts purely by
 * `modified_at`, so this mutation does NOT reorder or relabel that list — it
 * updates the daemon's persisted `agent_session_id`, which surfaces instead
 * through the activity snapshot's `sessions[0]` (see `primarySessionId` in
 * `live-question.ts` — the actual confirmation signal, never the response
 * body: `WorkspaceStateView` deliberately never exposes `agent_session_id`).
 * Invalidating `["activity"]` here mirrors every other mutation's
 * `invalidateWorkspace` convention — the daemon's `session_remapped` event
 * also arrives over SSE, but invalidating client-side keeps the poll-fallback
 * path (and any surface not currently streaming) in sync too.
 */
export function useRemapSession(workspaceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (sessionId: string) => client.remapSession(workspaceId, sessionId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["sessions", workspaceId] });
      void queryClient.invalidateQueries({ queryKey: ["workspace", workspaceId] });
      void queryClient.invalidateQueries({ queryKey: ["activity"] });
    },
  });
}

// ─── Lifecycle mutations (workspace parity, #56) ─────────────────────────────

/**
 * Invalidate every cache a lifecycle mutation can stale. The SSE stream already
 * carries the change for the list surfaces (the daemon's bus emits on each op),
 * but the detail page rides polled queries — invalidating both keeps every
 * surface fresh whether or not a stream is connected. One helper so the four
 * mutations + create can't drift on which keys they refresh.
 */
function invalidateWorkspace(queryClient: ReturnType<typeof useQueryClient>, id?: string) {
  void queryClient.invalidateQueries({ queryKey: ["workspaces"] });
  void queryClient.invalidateQueries({ queryKey: ["activity"] });
  if (id) {
    void queryClient.invalidateQueries({ queryKey: ["workspace", id] });
    void queryClient.invalidateQueries({ queryKey: ["peek", id] });
    void queryClient.invalidateQueries({ queryKey: ["commits", id] });
  }
}

/**
 * The full per-workspace lifecycle as one cohesive action object: pause / resume
 * / respawn / kill, each a TanStack mutation sharing one cache-invalidation
 * policy. Bundling them is the "state + the methods over it as one unit"
 * principle — the four ops are the same concern (this workspace's lifecycle) and
 * must invalidate identically, so they live together rather than as four loose
 * hooks a caller wires up by hand. The UI gates *which* it shows via the pure
 * `availableActions`; the engine is the real precondition gate.
 */
export function useWorkspaceActions(workspaceId: string) {
  const queryClient = useQueryClient();
  const onSuccess = () => invalidateWorkspace(queryClient, workspaceId);
  return {
    pause: useMutation({
      // Explicit `boolean` variable (not a defaulted param) so TanStack types
      // `mutate(force)` as taking the flag rather than collapsing it to `void`.
      mutationFn: (force: boolean) => client.pauseWorkspace(workspaceId, force),
      onSuccess,
    }),
    resume: useMutation({
      mutationFn: () => client.resumeWorkspace(workspaceId),
      onSuccess,
    }),
    respawn: useMutation({
      mutationFn: () => client.respawnWorkspace(workspaceId),
      onSuccess,
    }),
    kill: useMutation({
      // `null` defers the delete-branch choice to the engine's provenance
      // default; an explicit boolean (from the confirm dialog) overrides it.
      mutationFn: (deleteBranch: boolean | null) =>
        client.killWorkspace(workspaceId, deleteBranch),
      onSuccess,
    }),
  };
}

/**
 * Create a workspace from a full `CreateWorkspaceRequest`. On success the list +
 * activity caches invalidate so the new card appears immediately even before the
 * SSE `workspace_changed` delta lands. Refusals (unknown agent, branch conflict,
 * a 422 from a bad branch plan) surface as the typed `GroveProtocolError`.
 */
export function useCreateWorkspace() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (req: CreateWorkspaceRequest) => client.createWorkspace(req),
    onSuccess: () => invalidateWorkspace(queryClient),
  });
}

/**
 * Configured agents for one repo — the create form's agent picker. `repo` null
 * keeps the query disabled (the form hasn't picked a project yet). Agent config
 * changes rarely, so a 30 s staleTime avoids re-fetching as the user toggles
 * other form fields.
 */
export function useAgents(repo: string | null) {
  return useQuery<AgentSummaryView[]>({
    queryKey: ["agents", repo],
    queryFn: () => client.listAgents(repo as string),
    enabled: Boolean(repo),
    staleTime: 30_000,
  });
}

/**
 * Branches for one repo at one scope — the create form's Existing/Remote
 * pickers. `enabled` lets the form fetch a scope only when its branch mode needs
 * it (Existing → local, Remote → remote), so picking Auto fetches nothing.
 */
export function useBranches(repo: string | null, scope: "local" | "remote", enabled = true) {
  return useQuery<BranchInfo[]>({
    queryKey: ["branches", repo, scope],
    queryFn: () => client.listBranches(repo as string, scope),
    enabled: Boolean(repo) && enabled,
    staleTime: 15_000,
  });
}

/**
 * The cross-project Activity Dashboard stream.
 *
 * Primary path: a cookie-auth `EventSource` to the BFF `/api/grove/events` — the
 * daemon sends a `snapshot` on connect then live deltas, which the pure
 * `applyDashboardEvent` reducer folds into one snapshot. EventSource handles
 * reconnect (and `Last-Event-ID` replay) for free.
 *
 * Fallback: when SSE can't connect (an intermediary strips event-streams, or
 * we're in jsdom which has no `EventSource`), a `/activity` poll keeps the wall
 * live. It's enabled only while disconnected, so the happy path costs one stream
 * and zero polls.
 */
export interface ActivityStream {
  snapshot: DashboardSnapshotView | null;
  connected: boolean;
  /** Epoch ms of the most recent SSE event of ANY kind (incl. heartbeat); null until the first event. */
  lastEventAt: number | null;
  /** Last poll-fallback error while disconnected — the daemon-unreachable affordance. Null while the stream is live. */
  error: Error | null;
  /** Force a fresh pull: tear down + reconnect the stream (server resends `snapshot`) and reset the poll fallback. */
  refresh: () => void;
}

// The daemon heartbeats every 15 s; anything older means the tab slept through
// at least one beat, so the EventSource is presumed dead even if it never
// fired `onerror` (background-tab throttling / network sleep swallow it).
const STREAM_STALE_MS = 20_000;

export function useActivityStream(): ActivityStream {
  const [snapshot, setSnapshot] = useState<DashboardSnapshotView | null>(null);
  const [connected, setConnected] = useState(false);
  const [lastEventAt, setLastEventAt] = useState<number | null>(null);
  // Ref twin of `lastEventAt` so the visibilitychange listener reads the
  // current value without re-subscribing on every event.
  const lastEventAtRef = useRef<number | null>(null);
  // Ref twin of `snapshot` so the `session_activity` listener can test whether
  // an incoming delta is for a known workspace without re-subscribing on every
  // snapshot change (the effect re-runs only on reconnect).
  const snapshotRef = useRef<DashboardSnapshotView | null>(null);
  snapshotRef.current = snapshot;
  const stamp = useCallback(() => {
    lastEventAtRef.current = Date.now();
    setLastEventAt(lastEventAtRef.current);
  }, []);
  // Nonce that, when bumped, re-runs the EventSource effect — a fresh connection
  // makes the daemon resend the `snapshot` from scratch (the refresh mechanism).
  const [reconnectKey, setReconnectKey] = useState(0);
  const queryClient = useQueryClient();

  useEffect(() => {
    if (typeof EventSource === "undefined") return; // jsdom / SSR → poll fallback only
    const es = new EventSource(`${GroveClient._basePath}/events`);
    // Every event — snapshot, delta, lifecycle, heartbeat — refreshes the
    // dashboard-wide "last updated" clock, so the UI proves the stream is live.
    const fold = (e: MessageEvent) => {
      stamp();
      setSnapshot((prev) => applyDashboardEvent(prev, JSON.parse(e.data) as DashboardEvent));
    };
    // Lifecycle wake-up (or an out-of-band create the poll surfaced as a
    // `session_activity` for a workspace we don't have yet, #49) — re-fetch
    // the full snapshot so the new row appears and gone rows drop.
    const refetch = () => {
      stamp();
      client.getActivity().then(setSnapshot).catch(() => undefined);
    };
    es.addEventListener("snapshot", fold);
    es.addEventListener("session_activity", (e: MessageEvent) => {
      // A `session_activity` for an unknown workspace means a separate process
      // (a second TUI, the MCP server, the CLI) created it — the daemon's poll
      // emits `session_activity`, not the bus-bridged `workspace_changed`, for
      // those. The pure reducer drops it to keep the wall stable, so promote it
      // to a full re-fetch instead of swallowing the create.
      const event = JSON.parse(e.data) as DashboardEvent;
      const id = event.workspace?.state.id;
      if (id && !snapshotHasWorkspace(snapshotRef.current, id)) {
        refetch();
        return;
      }
      fold(e);
    });
    es.addEventListener("workspace_changed", refetch);
    es.addEventListener("heartbeat", stamp);
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    return () => es.close();
  }, [reconnectKey, stamp]);

  // Stale-tab self-heal: a backgrounded tab's EventSource can die without ever
  // firing `onerror`, leaving a wall that looks live but is frozen. On return
  // to the tab, a missed heartbeat means reconnect — the daemon then resends a
  // fresh `snapshot` on the new connection.
  useEffect(() => {
    const onVisibilityChange = () => {
      if (document.visibilityState !== "visible") return;
      const last = lastEventAtRef.current;
      if (last !== null && Date.now() - last > STREAM_STALE_MS) {
        setReconnectKey((n) => n + 1);
      }
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => document.removeEventListener("visibilitychange", onVisibilityChange);
  }, []);

  const poll = useQuery<DashboardSnapshotView>({
    queryKey: ["activity"],
    queryFn: () => client.getActivity(),
    refetchInterval: 4_000,
    enabled: !connected,
  });
  useEffect(() => {
    if (!connected && poll.data) {
      setSnapshot(poll.data);
      stamp();
    }
  }, [connected, poll.data, stamp]);

  const refresh = useCallback(() => {
    // Force-resend the snapshot over a fresh connection, and re-pull the poll
    // fallback so both transports re-fetch from scratch on click.
    setReconnectKey((n) => n + 1);
    void queryClient.invalidateQueries({ queryKey: ["activity"] });
  }, [queryClient]);

  return {
    snapshot,
    connected,
    lastEventAt,
    // While the stream is live the poll is disabled, so its stale error (if
    // any) must not surface as "daemon unreachable".
    error: connected ? null : poll.error,
    refresh,
  };
}

/**
 * Stream one workspace's agent pane for the dashboard's focused live view.
 *
 * Primary path (mirrors `useActivityStream`): a cookie-auth `EventSource` to the
 * BFF `/workspaces/{id}/pane/stream` — the daemon pushes diff-guarded
 * `pane_snapshot` frames (~1 Hz, an event only when the ANSI changed) which we
 * store as the latest `WorkspacePaneView`. The connection is the "dispose the
 * prior one on focus switch" mechanism: a new `id` (or `enabled → false`) tears
 * the EventSource down, and the stored pane resets to `undefined` so a stale
 * pane from the previously-focused workspace never flashes on the new one.
 *
 * Fallback: in jsdom/SSR (no `EventSource`) or when the stream can't connect, a
 * one-shot `getPane` poll keeps the pane live, enabled only while disconnected.
 *
 * Gating is client-side: the page enables this for the single focused WORKING
 * card only (page-level `liveId`), so the wall streams one pane, never N.
 */
export function useWorkspacePane(id: string | null, enabled: boolean) {
  const [pane, setPane] = useState<WorkspacePaneView | undefined>(undefined);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    // Reset on every connection-key change (id switch / disable / unmount) so a
    // stale pane from the prior focus can't flash on the new one.
    setPane(undefined);
    setConnected(false);
    if (!enabled || !id) return;
    if (typeof EventSource === "undefined") return; // jsdom / SSR → poll fallback only
    const es = new EventSource(GroveClient.paneStreamUrl(id));
    es.addEventListener("pane_snapshot", (e: MessageEvent) => {
      const event = JSON.parse(e.data) as DashboardEvent;
      setPane(event.pane ?? undefined);
    });
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    return () => es.close();
  }, [id, enabled]);

  const poll = useQuery<WorkspacePaneView>({
    queryKey: ["pane", id],
    queryFn: () => client.getPane(id as string),
    refetchInterval: 1_000,
    // Poll only when SSE isn't carrying the pane (jsdom / SSR / no stream).
    enabled: Boolean(id) && enabled && !connected,
  });

  const data = pane ?? poll.data;
  return { data, isLoading: enabled && Boolean(id) && data === undefined };
}
