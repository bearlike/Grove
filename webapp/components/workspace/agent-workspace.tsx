"use client";

import dynamic from "next/dynamic";
import { useEffect, type ReactNode } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable";
import { WorkPanel } from "@/components/workspace/work-panel";
import type { AgentTab } from "@/components/workspace/view-switcher";
import type { AgentLiveStatus } from "@/lib/grove/agent-activity";
import type {
  AgentActivityState,
  CommitSummaryView,
  DashboardSnapshotView,
  WorkspacePeekView,
} from "@/lib/grove/types";

// Streamdown is a ~460 kB async chunk and the only consumer is the chat panel —
// keep it behind `next/dynamic` here (the component that renders it) so the
// dynamic boundary travels with the leaf, not the page.
const ChatPanel = dynamic(
  () => import("@/components/chat/chat-panel").then((m) => m.ChatPanel),
  { ssr: false, loading: () => <Skeleton className="h-full min-h-[20rem] w-full" /> },
);

/**
 * The dominant surface of the detail page: the agent transcript and the work
 * panel — panes ONLY since the chrome teardown (#130). The page owns every bit
 * of view state (which tab, single vs. split) and the view switcher rides the
 * header identity cluster, so this component just renders the side-by-side
 * split (`showSplit`) or the single active pane (`tab`). No strip row, no
 * `TabsList`, no status line.
 *
 * The Resizable split is the canonical primitive (never a hand-rolled resizer),
 * its ratio persisted via `autoSaveId` — this contract is FROZEN across the
 * ADE work-panel change (#142): only the right pane's CONTENT changed, from a
 * bare `TerminalPane` to the tabbed `WorkPanel` (Terminal/Diff/Info). The
 * `min-h-0`/`min-w-0` chains thread through verbatim so a long transcript or a
 * wide terminal scrolls inside its own pane and never widens the document.
 *
 * `peek`/`live`/`commits` are the same page-owned reads `ContextBar` already
 * consumes for the identity strip — threaded here too because the `Diff`/`Info`
 * tabs need the same git/activity data the strip summarizes. One fetch, two
 * homes, never a second request.
 *
 * ⌘/Ctrl+J toggles the work panel (design §4.2: "one obvious control — `⟩` /
 * `⌘J` — slides it in"). The page owns the actual tab/view state (so it can
 * apply the right toggle semantics per breakpoint); this component only
 * listens for the chord and forwards it via `onTogglePanel`, mirroring the
 * `[` sidebar-toggle listener pattern in the shell layout. Ignored while a
 * form field has focus so it never hijacks normal typing.
 *
 * Session selection + the activity snapshot are page-owned (one EventSource
 * per route); the selected `sessionId`, `activitySnapshot`, and `agentState`
 * pass straight through to the ChatPanel. Seam: `agent-panel`.
 */
export function AgentWorkspace({
  workspaceId,
  peek,
  live,
  commits,
  commitsLoading,
  tab,
  showSplit,
  sessionId,
  activitySnapshot,
  agentState,
  emptyStatePicker,
  onTogglePanel,
}: {
  workspaceId: string;
  /** The page's polled peek — feeds both the transcript's session cascade context and the work panel's Terminal/Diff/Info tabs. */
  peek: WorkspacePeekView;
  live: AgentLiveStatus;
  commits: CommitSummaryView[] | undefined;
  commitsLoading?: boolean;
  /** The active pane in single-pane view; `null` renders the loading skeleton. */
  tab: AgentTab | null;
  showSplit: boolean;
  sessionId: string | null;
  /** The live dashboard snapshot (page-owned) the chat panel reads for pending questions. */
  activitySnapshot: DashboardSnapshotView | null;
  agentState: AgentActivityState;
  /** The page-wired track picker (#132) shown in the transcript's empty state when no session is tracked but candidates exist. */
  emptyStatePicker?: ReactNode;
  /** ⌘/Ctrl+J — the page decides what "toggle" means for the current breakpoint/view. */
  onTogglePanel?: () => void;
}) {
  useEffect(() => {
    if (!onTogglePanel) return;
    function onKey(e: KeyboardEvent) {
      if (e.key.toLowerCase() !== "j" || !(e.metaKey || e.ctrlKey) || e.shiftKey || e.altKey) return;
      const target = e.target as HTMLElement | null;
      if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return;
      e.preventDefault();
      onTogglePanel?.();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onTogglePanel]);

  const transcript = (
    <ChatPanel
      workspaceId={workspaceId}
      sessionId={sessionId}
      snapshot={activitySnapshot}
      agentState={agentState}
      emptyStatePicker={emptyStatePicker}
    />
  );
  const panel = (
    <WorkPanel peek={peek} live={live} commits={commits} commitsLoading={commitsLoading} />
  );

  return (
    <div data-testid="agent-panel" className="flex min-h-0 min-w-0 flex-1 flex-col">
      {showSplit ? (
        <ResizablePanelGroup
          direction="horizontal"
          autoSaveId="grove-detail-split"
          className="min-h-0 flex-1"
        >
          <ResizablePanel defaultSize={55} minSize={30} className="flex min-h-0 min-w-0 flex-col">
            {transcript}
          </ResizablePanel>
          <ResizableHandle withHandle />
          <ResizablePanel defaultSize={45} minSize={25} className="flex min-h-0 min-w-0 flex-col">
            {panel}
          </ResizablePanel>
        </ResizablePanelGroup>
      ) : (
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          {tab === null && <Skeleton className="h-full min-h-[20rem] w-full" />}
          {tab === "transcript" && transcript}
          {tab === "terminal" && panel}
        </div>
      )}
    </div>
  );
}
