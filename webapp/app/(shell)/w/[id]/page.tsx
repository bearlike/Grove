"use client";

import { use, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useQueryClient } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { Skeleton } from "@/components/ui/skeleton";
import { useHeaderSlot } from "@/components/layout/header-slot";
import { ContextBar } from "@/components/workspace/context-bar";
import { AgentWorkspace } from "@/components/workspace/agent-workspace";
import { SessionPicker } from "@/components/chat/session-picker";
import {
  ViewSwitcher,
  type AgentTab,
  type AgentView,
} from "@/components/workspace/view-switcher";
import { AgentLiveStatus } from "@/lib/grove/agent-activity";
import { turnsProgressFingerprint } from "@/lib/grove/activity-stream";
import { primarySessionId } from "@/lib/grove/live-question";
import { GroveProtocolError } from "@/lib/grove/client";
import {
  useActivityStream,
  useRemapSession,
  useWorkspaceCommits,
  useWorkspacePeek,
  useWorkspaceSessions,
  useWorkspaceSessionCandidates,
} from "@/lib/grove/hooks";

/** SSR-safe `min-width` match; jsdom's matchMedia stub reports false → mobile band. */
function useMinWidth(px: number): boolean {
  const [match, setMatch] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia(`(min-width:${px}px)`);
    const sync = () => setMatch(mq.matches);
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, [px]);
  return match;
}

/**
 * The ONE seam deciding what the agent surface shows (#159) — everything else
 * (ViewSwitcher, AgentWorkspace) just renders whatever this returns. Collapses
 * what used to be two scattered conditionals (`defaultTab`/`view` inline in the
 * component) into a single decision table:
 *
 *   default tab  = sessionResolved ? "transcript" : "terminal" (null while loading, for the skeleton)
 *   default view = "tabs" (single pane) ALWAYS — split is opt-in via the ViewSwitcher, never a default
 *   explicit choice (chosenTab / chosenView) wins over both defaults, for the page's lifetime
 *
 * Split used to default open on lg+ once a session resolved (#124/#130); the
 * product ruling for #159 is transcript-first single-pane everywhere, split
 * reachable but never assumed.
 */
function resolvePaneView(
  sessionResolved: boolean,
  sessionsLoading: boolean,
  isLg: boolean,
  chosenTab: AgentTab | null,
  chosenView: AgentView | null,
): { tab: AgentTab | null; view: AgentView; showSplit: boolean } {
  const defaultTab: AgentTab | null = sessionsLoading
    ? null
    : sessionResolved
      ? "transcript"
      : "terminal";
  const tab = chosenTab ?? defaultTab;
  const view = chosenView ?? "tabs";
  return { tab, view, showSplit: isLg && view === "split" };
}

/**
 * The workspace detail page — the three-zone session layout (ADE reframe, #138):
 * `rail | transcript column | work-panel`. The rail + header now come from the
 * shared shell layout (this route moved INTO the `(shell)` group), so this page
 * renders only zones 2+3 (the existing `AgentWorkspace` split, still fully
 * functional) plus the session's own chrome.
 *
 * Header cluster: the page owns the session data, so it builds the `ContextBar`
 * identity + view cluster and PORTALS it into the shared header's middle slot
 * (`useHeaderSlot` → `createPortal`). This keeps exactly one `context-bar` mount,
 * inside `<header>`, at every width — the old below-lg band is still gone. `back`
 * is route-derived by the shell, so the page no longer renders its own `<Header>`.
 *
 * Live agent state: it rides the `ContextBar` in the header now (#153) — the
 * state mark leads the identity trigger, and an sr-only `aria-live` region there
 * announces the state word on change. The separate in-page `context-task`
 * subheader is DELETED: it re-duplicated the state and, worse, showed a
 * `taskLine` (the "happening now" prompt text) that the #136 bound already ruled
 * off the announced surface — the header cluster is the single home now. No
 * ui-store `sessionStatus` mirror is involved (that slice died with the status
 * bar). Per the #136 bound, the header's live region carries ONLY the state
 * word, never the raw task/prompt text.
 *
 * The page owns ALL cross-cutting session state so the header cluster can ride
 * the shell: the session-selection cascade + remap (#121, lifted from ChatPanel),
 * the single `useActivityStream` subscription (one EventSource per route) —
 * which also drives the transcript's SSE-invalidation (`turnsProgressFingerprint`,
 * #166), so the turns cache doesn't wait out its own poll to catch up — and ALL
 * view state (which tab, single vs. split — see `resolvePaneView` above, #159).
 * The AgentWorkspace below is panes-only; the ChatPanel gets the resolved
 * session as props.
 *
 * At EVERY breakpoint the page is a fixed-height flex column — `100dvh` minus the
 * h-13 header (52px = 3.25rem; the status bar is gone, so this is the ONLY chrome
 * subtracted) — whose panes scroll internally. The `detail-panel` is a BARE
 * full-bleed column on the `bg-background` canvas.
 *
 * NEVER add `overflow-hidden` to the page column or `detail-panel`: a
 * fixed-height, overflow-clipped ancestor holding actionable buttons hangs
 * Playwright's pre-click scrollIntoViewIfNeeded (the documented scroll-trap); the
 * leaf panes clip their own scroll instead.
 */
export default function DetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const { data, isLoading, isError, error } = useWorkspacePeek(id);
  const { data: commits, isLoading: commitsLoading } = useWorkspaceCommits(id);
  const { data: sessions, isLoading: sessionsLoading } = useWorkspaceSessions(id);
  const { snapshot } = useActivityStream();

  // Session selection cascade, lifted from the chat panel (#121 → #130) so the
  // picker (and now the session rail, #140) can ride the header cluster: the
  // `?s=` URL param wins when present (a rail row navigates to `/w/{id}?s={sid}`),
  // then an explicit in-page pick, then the daemon's tracked primary (off the
  // activity snapshot — durable across a reload), then the plain list's head.
  const searchParams = useSearchParams();
  const urlSessionId = searchParams.get("s");
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(urlSessionId);
  // Keep the selection following the URL: a rail click that changes `?s=` (same
  // workspace) or a jump to another workspace re-seeds it. An in-page picker
  // choice (which touches neither `id` nor the param) survives untouched.
  useEffect(() => {
    setSelectedSessionId(urlSessionId);
  }, [id, urlSessionId]);
  const backendPrimaryId = primarySessionId(snapshot, id);

  // What the daemon's OWN tracked/attributed data resolves to (the gated list +
  // the snapshot primary) — the healthy path. When this is null the workspace
  // has no usable session to show (the dead-pointer bug, #132), and ONLY then do
  // we pay the extra ungated candidate scan so a remap out of the dead end exists.
  const gatedActive =
    (selectedSessionId && sessions?.find((s) => s.session_id === selectedSessionId)) ||
    (backendPrimaryId && sessions?.find((s) => s.session_id === backendPrimaryId)) ||
    sessions?.[0] ||
    null;
  const { data: candidates } = useWorkspaceSessionCandidates(id, {
    enabled: !sessionsLoading && !gatedActive,
  });

  // The ungated candidate list is also the fallback resolver: right after a
  // candidate is pinned the gated list hasn't refetched yet, so resolving the
  // freshly-picked id against candidates keeps the transcript rendering across
  // that invalidation gap (candidates ⊇ the gated set by construction).
  const active =
    gatedActive ||
    (selectedSessionId && candidates?.find((s) => s.session_id === selectedSessionId)) ||
    (backendPrimaryId && candidates?.find((s) => s.session_id === backendPrimaryId)) ||
    null;
  const sessionId = active?.session_id ?? null;
  const agentState = active?.activity.state ?? "unknown";

  // Live status describes the SELECTED session (coherence: the badge + task line
  // match the transcript you're looking at), not blindly the newest session.
  const live = AgentLiveStatus.of(active?.activity ?? null);

  // SSE-invalidate the transcript the instant a turn actually advances (#166):
  // `useSessionTurns`' refetch interval is a backstop, not the freshness
  // mechanism — the terminal pane streams at ~1s while turns only refetch on
  // that timer, so a user watching the pane work sees the transcript trail it.
  // `turnsProgressFingerprint` reads the SSE snapshot the page already holds
  // (no second EventSource); when it changes for the OPEN session, invalidate
  // that exact `["turns", …]` cache entry. Same skip-first-run shape as the
  // rail's own fingerprint invalidation (`SessionRail`) — mount must not
  // double-fetch what `useSessionTurns` already fetches on its own.
  const queryClient = useQueryClient();
  const turnsFingerprint = turnsProgressFingerprint(snapshot, id, sessionId);
  const firstTurnsRun = useRef(true);
  useEffect(() => {
    if (firstTurnsRun.current) {
      firstTurnsRun.current = false;
      return;
    }
    void queryClient.invalidateQueries({ queryKey: ["turns", id, sessionId] });
  }, [turnsFingerprint, queryClient, id, sessionId]);

  // Pin a picked session as the tracked primary (#121) — a durable remap,
  // separate from just switching which session the panel shows.
  const remapSession = useRemapSession(id);
  const remapErrorText =
    remapSession.error instanceof GroveProtocolError
      ? remapSession.error.message
      : remapSession.error
        ? "Could not pin the session."
        : null;
  const handleMakePrimary = (sid: string) => {
    remapSession.mutate(sid, { onSuccess: () => setSelectedSessionId(sid) });
  };

  // View state (page-owned): see `resolvePaneView` above for the one decision
  // table this derives from (#159 — transcript-first single pane everywhere;
  // split is opt-in, never a default). A user's explicit tab/view choice wins
  // after, for the page's lifetime.
  const [chosenTab, setChosenTab] = useState<AgentTab | null>(null);
  const [chosenView, setChosenView] = useState<AgentView | null>(null);

  // `isLg` gates ONLY split availability — the identity cluster rides the
  // header at every width, so placement no longer depends on it (one breakpoint
  // story: lg+ = split available, below lg = tabs only). Below-lg first paint is
  // fine (SSR-safe hook resolves false first).
  const isLg = useMinWidth(1024);
  const { tab, view, showSplit } = resolvePaneView(
    Boolean(sessions && sessions.length > 0),
    sessionsLoading,
    isLg,
    chosenTab,
    chosenView,
  );

  // An explicit tab click means "focus this one pane" — drop out of split so
  // the click has a visible effect; the view toggle is the way back to split.
  const handleTabChange = (t: AgentTab) => {
    setChosenTab(t);
    setChosenView("tabs");
  };

  // ⌘/Ctrl+J (the work panel's `onTogglePanel`): below lg, swap the focused pane
  // between transcript and terminal; on lg, collapse the split back to the
  // transcript tab or open it (agent E's work-panel contract).
  const handleTogglePanel = () => {
    if (!isLg) {
      setChosenTab(tab === "transcript" ? "terminal" : "transcript");
      setChosenView("tabs");
      return;
    }
    if (view === "split") {
      setChosenView("tabs");
      setChosenTab("transcript");
    } else {
      setChosenView("split");
    }
  };

  // Both cluster controls are page-wired (they read page state) and handed to
  // whichever ContextBar mounts. The picker self-returns null when there is
  // neither a switch (>1 session) nor a track (candidates while stuck) to offer.
  const viewSwitcher = (
    <ViewSwitcher
      tab={tab}
      view={view}
      isLg={isLg}
      onTabChange={handleTabChange}
      onViewChange={setChosenView}
    />
  );

  // Offer the candidate pick ONLY while nothing resolves to show (`sessionId`
  // null) — once a candidate is pinned `active` resolves it and the affordance
  // retires. Healthy/switch cases never fetch candidates, so the list is empty.
  const candidateList = candidates ?? [];
  const showTrack = sessionId === null && candidateList.length > 0;
  const remapPending = remapSession.isPending ? (remapSession.variables ?? null) : null;

  // The empty-state / stuck-recovery picker (ChatPanel) still uses the full
  // Popover wrapper — a fresh element per mount since a React node can't mount
  // twice. It self-returns null unless there's a candidate to track.
  const emptyStatePicker = showTrack ? (
    <SessionPicker
      sessions={sessions ?? []}
      candidates={candidateList}
      selectedId={sessionId}
      onSelect={setSelectedSessionId}
      onMakePrimary={handleMakePrimary}
      pendingId={remapPending}
      error={remapErrorText}
    />
  ) : undefined;

  // The identity cluster rides the shared app header at EVERY breakpoint via a
  // portal into the header's middle slot — exactly one mount, inside `<header>`,
  // so the `context-bar` testid never doubles. `slotEl` is null for the first
  // tick (before the header commits its ref); the portal simply waits.
  const slotEl = useHeaderSlot();
  const identityCluster =
    data && slotEl
      ? createPortal(
          <ContextBar
            peek={data}
            live={live}
            onKilled={() => router.push("/")}
            viewSwitcher={viewSwitcher}
          />,
          slotEl,
        )
      : null;

  return (
    <div className="flex h-[calc(100dvh-3.25rem)] min-h-0 min-w-0 flex-col">
      {identityCluster}
      {isLoading && <DetailSkeleton />}
      {isError && (
        <div
          role="alert"
          className="m-4 rounded-md border border-[var(--status-error)] bg-[var(--status-error)]/10 p-4 text-sm text-foreground"
        >
          Could not load workspace: {String((error as Error).message)}
        </div>
      )}

      {data && (
        // The live agent state + identity now ride the header's ContextBar
        // (#153) — the old `context-task` subheader is deleted, so the panes get
        // the full column below the header. `detail-panel` is a BARE full-bleed
        // column on the `bg-background` canvas.
        <div
          data-testid="detail-panel"
          className="flex min-h-0 min-w-0 flex-1 flex-col"
        >
          <AgentWorkspace
            workspaceId={id}
            peek={data}
            live={live}
            commits={commits}
            commitsLoading={commitsLoading}
            tab={tab}
            showSplit={showSplit}
            sessionId={sessionId}
            activitySnapshot={snapshot}
            agentState={agentState}
            emptyStatePicker={emptyStatePicker}
            onTogglePanel={handleTogglePanel}
          />
        </div>
      )}
    </div>
  );
}

function DetailSkeleton() {
  return (
    <div className="flex flex-1 flex-col gap-3 p-4">
      <Skeleton className="h-16 w-full" />
      <Skeleton className="min-h-[24rem] flex-1" />
    </div>
  );
}
