"use client";

import { Activity, useCallback, useEffect, useRef, useState } from "react";
import { GlobeIcon, SquareIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useDefaultLayout, type LayoutStorage } from "react-resizable-panels";

import { AgentStatus } from "@/components/elements/agent-status";
import { ErrorState } from "@/components/elements/error-state";
import { ShellHeader } from "@/components/grove/shell/shell-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  agentStatusProps,
  findWorkspaceActivity,
  primarySessionId,
} from "@/lib/grove/adapters";
import {
  useActivityStream,
  useRemapSession,
  useSessionTurns,
  useWorkspaceCommits,
  useWorkspacePeek,
  useWorkspaceSessions,
} from "@/lib/grove/hooks";
import { useGroveThread } from "@/lib/grove/runtime";

import { ProvisionProgress } from "./provision-progress";
import { Transcript } from "./transcript";
import { TranscriptSkeleton } from "./transcript-skeleton";
import { WorkPanel } from "./work-panel";
import {
  panesShown,
  storedView,
  storedWorkTab,
  visiblePane,
  resolvedWorkspaceSelection,
  type PanelTab,
  type PaneView,
  type WorkspaceSelection,
} from "./selectors";
import { useMinWidth } from "./use-min-width";

const SPLIT_MIN_WIDTH = 1024;

/** The split's persisted layout key, and the panel ids that layout is keyed by
 * — the ids must match the panels mounted at that moment or the restore lands
 * on the wrong panes. */
const SPLIT_STORAGE_ID = "grove-workspace-split";
const SPLIT_PANELS = ["transcript", "work"];

/**
 * `useDefaultLayout` reads its storage DURING RENDER, and the server has no
 * `localStorage` — the default storage crashes the whole route there. The
 * server therefore restores nothing, which costs no layout shift: the split
 * only mounts behind a `matchMedia` check that is false until an effect runs,
 * so it never renders on the server or on the first client paint.
 */
const SPLIT_STORAGE: LayoutStorage = {
  getItem: (key) =>
    typeof window === "undefined" ? null : window.localStorage.getItem(key),
  setItem: (key, value) => {
    if (typeof window !== "undefined") window.localStorage.setItem(key, value);
  },
};

/**
 * Which pane and work tab a workspace was last left on, keyed PER WORKSPACE
 * ID — a bare "last pane" key would apply one workspace's choice to a
 * different one, which is worse than always resetting to the default. Two
 * independent keys, not one JSON blob: `view` and `workTab` change on
 * different events (the switcher vs. the work-panel tab strip), and writing
 * only the one that changed avoids a stale read of the other clobbering it
 * back on the next write.
 */
const viewStorageKey = (id: string) => `grove-workspace-view:${id}`;
const workTabStorageKey = (id: string) => `grove-workspace-work-tab:${id}`;

/** Defensive `localStorage` read: missing key, no `window` (SSR/edge), and a
 * disabled or throwing store (private browsing in some browsers) all read as
 * `null`, which remains "no reader choice" and lets the live default decide. */
function readPaneStorage(key: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

/** Best-effort write: a full or disabled store must not break switching
 * panes, so a thrown `setItem` is swallowed rather than surfaced. */
function writePaneStorage(key: string, value: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // best-effort persistence only
  }
}

/**
 * The workspace surface: the conversation on the left, the work on the right.
 *
 * ONE row of chrome above the content. The pane switcher, the agent's state and
 * the interrupt all ride the header's `actions` slot — that is what the slot is
 * for, and four stacked strips left a gap where the transcript should have
 * been. The plan card moved to the composer, in the thread's own footer.
 *
 * How the page gets its BOUNDED height — without a magic header constant — is
 * explained on `Surface` below, which is what enforces it.
 *
 * Owns its `ShellHeader` rather than letting the route render it, because the
 * breadcrumb wants the workspace TITLE and only the peek carries one — the
 * route has nothing but an id.
 */
export function Workspace({ id }: { id: string }) {
  const router = useRouter();
  const peek = useWorkspacePeek(id);
  const commits = useWorkspaceCommits(id);
  const { snapshot } = useActivityStream();
  const activity = findWorkspaceActivity(snapshot, id);
  const sessionId = primarySessionId(snapshot, id);

  const sessions = useWorkspaceSessions(id);
  const { mutate: remapMutate } = useRemapSession(id);
  const switchSession = useCallback(
    (next: string) => remapMutate(next),
    [remapMutate],
  );
  const thread = useGroveThread({
    workspaceId: id,
    sessionId,
    snapshot,
    sessions: sessions.data,
    onSwitchSession: switchSession,
  });

  // This is intentionally a second observer of the same query `useGroveThread`
  // reads. React Query deduplicates it, and the raw turn count is the only
  // reliable boundary between an unborn transcript and a real (possibly later
  // growing) one.
  const turns = useSessionTurns(id, sessionId).query;
  const hasTranscript = (turns.data?.turns.length ?? 0) > 0;
  const wideEnoughToSplit = useMinWidth(SPLIT_MIN_WIDTH);
  const [selection, setSelection] = useState<WorkspaceSelection>({
    view: null,
    workTab: null,
  });
  const selectionWorkspaceId = useRef(id);

  // Navigating between workspace routes can retain this component instance. A
  // selection belongs to one workspace, never to the route slot, so clear it
  // synchronously before the new workspace first paints. The ref is a route
  // guard rather than UI state: it lets the restore effect reject a stale route
  // without adding a second render-state field.
  if (selectionWorkspaceId.current !== id) {
    selectionWorkspaceId.current = id;
    setSelection({ view: null, workTab: null });
  }

  // `null` is a deliberate state, not an encoded default: only it follows the
  // transcript rule. A stored or freshly clicked value remains authoritative
  // when the first turn arrives asynchronously.
  const { view, workTab } = resolvedWorkspaceSelection(
    hasTranscript,
    selection,
  );
  // Never render `view` directly: below the breakpoint `split` is not on offer,
  // and holding it would leave no tab selected. See `visiblePane`.
  const paneView = visiblePane(view, wideEnoughToSplit);
  const showSplit = paneView === "split";

  // Latches true the first render a pane is shown, never back false: each
  // pane mounts once, lazily, on whichever visit — explicit or a width
  // fallback via `visiblePane` — first shows it, then stays mounted so a
  // later switch back finds it already there. Updated during render rather
  // than in an effect: an effect fires after paint, so the first frame a
  // pane becomes visible would still show it unmounted for one tick, which
  // is exactly the discontinuity this exists to remove. This is the
  // official React pattern for deriving state from the current render
  // (docs: "adjusting state when a prop changes") — it re-renders
  // synchronously before commit, and converges in one extra pass because
  // the second check always finds the flag already set.
  const shown = panesShown(paneView);
  const [transcriptMounted, setTranscriptMounted] = useState(() =>
    shown.includes("transcript"),
  );
  const [workMounted, setWorkMounted] = useState(() => shown.includes("work"));
  if (!transcriptMounted && shown.includes("transcript"))
    setTranscriptMounted(true);
  if (!workMounted && shown.includes("work")) setWorkMounted(true);

  // `WorkPanel` moves under a `ResizablePanelGroup` across
  // `SPLIT_MIN_WIDTH`, which unmounts it. Keeping its explicit selection here
  // preserves a tab chosen before that structural move.

  // Server and first client render must agree, so storage is restored only
  // after mount. Valid stored values are choices; absent and invalid ones stay
  // null and therefore continue to follow `resolvedWorkspaceSelection`. The
  // captured id rejects a stale effect from a route we have already left; the
  // functional update also makes a click that happens before this effect
  // authoritative.
  useEffect(() => {
    const stored = {
      view: storedView(readPaneStorage(viewStorageKey(id))),
      workTab: storedWorkTab(readPaneStorage(workTabStorageKey(id))),
    };
    setSelection((current) => {
      if (selectionWorkspaceId.current !== id) return current;
      return {
        view: current.view ?? stored.view,
        workTab: current.workTab ?? stored.workTab,
      };
    });
  }, [id]);

  const persistWorkTab = (tab: PanelTab) => {
    setSelection((current) => ({ ...current, workTab: tab }));
    writePaneStorage(workTabStorageKey(id), tab);
  };

  const changeView = (next: PaneView) => {
    setSelection((current) => ({ ...current, view: next }));
    writePaneStorage(viewStorageKey(id), next);
  };

  // The library's own persistence, not a hand-rolled localStorage read: it
  // already knows to restore before first paint and to ignore layout changes
  // that were not the user's doing.
  const splitLayout = useDefaultLayout({
    id: SPLIT_STORAGE_ID,
    panelIds: SPLIT_PANELS,
    storage: SPLIT_STORAGE,
    onlySaveAfterUserInteractions: true,
  });

  // FALLS BACK TO THE NOUN, NEVER THE ID — the same rule `page.tsx`'s
  // `generateMetadata` already states for the tab title, which this header had
  // quietly not been following. An id is unreadable, is an identifier in the
  // least private surface the app has, and names a row rather than a thing.
  // It also became visible more often once `loading.tsx` landed: that file
  // paints "Workspace", so falling back to the id here made the header flash
  // Workspace → 59d472a0b0ef… → the real title on every single navigation.
  const title = peek.data?.state.title ?? "Workspace";

  if (peek.isError) {
    return (
      <Surface>
        <ShellHeader title={title} />
        <ErrorState
          title="Couldn't load this workspace"
          detail={peek.error.message}
          retrying={peek.isFetching}
          onRetry={() => void peek.refetch()}
        />
      </Surface>
    );
  }
  if (!peek.data) {
    return (
      <Surface>
        <ShellHeader title={title} />
        <TranscriptSkeleton />
      </Surface>
    );
  }

  // A container build takes the whole surface: until it finishes there is no
  // tmux session to attach to and no transcript to read, and without saying so
  // the workspace reads offline for the entire build window.
  if (peek.data.state.status === "provisioning") {
    return (
      <Surface>
        <ShellHeader title={title} />
        <ProvisionProgress id={id} />
      </Surface>
    );
  }

  const transcript = (
    <Transcript
      workspaceId={id}
      sessionId={sessionId}
      thread={thread}
      narrow={showSplit}
    />
  );
  const workPanel = (
    <WorkPanel
      peek={peek.data}
      activity={activity}
      commits={commits.data}
      repoRoot={peek.data.state.repo_root}
      privileged={{ peek: peek.data, onKilled: () => router.push("/") }}
      tab={workTab}
      onTabChange={persistWorkTab}
    />
  );

  return (
    <Surface>
      <ShellHeader
        title={title}
        actions={
          <HeaderActions
            view={paneView}
            onChange={changeView}
            splitOffered={wideEnoughToSplit}
            status={activity}
            canInterrupt={thread.canInterrupt}
            onInterrupt={thread.interrupt}
            isPublic={peek.data.state.share_token !== null}
          />
        }
      />
      <div
        className="flex min-h-0 min-w-0 flex-1 flex-col"
        data-testid="workspace-page"
      >
        {showSplit ? (
          // Each pane is its own scroll owner, so a long transcript never drags
          // the terminal with it. `autoSaveId` persists the ratio; the vendored
          // handle brings keyboard resizing with it, which a pointer-drag div
          // would not.
          <ResizablePanelGroup
            id={SPLIT_STORAGE_ID}
            orientation="horizontal"
            defaultLayout={splitLayout.defaultLayout}
            onLayoutChanged={splitLayout.onLayoutChanged}
            className="min-h-0 flex-1"
          >
            {/* The pane's own div carries the layout and the surface: `Panel`
                owns its element's display and overflow outright and drops the
                className, so styling it there silently does nothing.
                `h-full` is the load-bearing part — the panel wraps its child in
                its OWN `overflow:auto` box, so a height-less child grows to its
                full content and that box becomes the scroll owner instead of
                the transcript. The visible symptom is a sticky composer
                stranded seventy thousand pixels down. */}
            <ResizablePanel id={SPLIT_PANELS[0]} defaultSize="55" minSize="30">
              <div className="bg-background flex h-full min-h-0 min-w-0 flex-col">
                {transcript}
              </div>
            </ResizablePanel>
            {/* NO `withHandle`. That prop draws a 12x16 bordered block with a
                grip icon parked in the middle of the divider, which is the
                "fat and unappealing" complaint: a hairline is what the divider
                should LOOK like, and a grip is a 90s affordance for a control
                that already tells you what it does by sitting between two
                panes.

                Thin to look at, easy to grab: the vendored `w-px` line stays,
                and `after:w-3` widens the INVISIBLE hit area from 4px to 12px.
                The `::after` belongs to the separator for hit-testing, which is
                also why plain `hover:`/`active:` fire from anywhere in that
                12px band rather than only on the 1px line.

                `hover`/`active`/`focus-visible` and not a library state
                attribute: react-resizable-panels documents exactly
                `data-separator`, `data-disabled`, `role` and ARIA on this
                element — there is no drag-state hook to bind to, and CSS
                `:active` already holds for the whole pointer drag. */}
            <ResizableHandle className="transition-colors after:w-3 hover:bg-ring focus-visible:bg-ring active:bg-ring" />
            <ResizablePanel id={SPLIT_PANELS[1]} defaultSize="45" minSize="25">
              <div className="bg-background flex h-full min-h-0 min-w-0 flex-col">
                {workPanel}
              </div>
            </ResizablePanel>
          </ResizablePanelGroup>
        ) : (
          <div className="flex min-h-0 min-w-0 flex-1 flex-col">
            {/* Both panes stay mounted once visited: `<Activity mode="hidden">`
                keeps a pane's component state and DOM intact while switching
                away from it, instead of tearing it down and rebuilding it from
                scratch — on a real transcript that rebuild is tens of
                thousands of nodes, which is the "choppy" delay this removes.
                The trade is deliberate: a hidden pane's nodes stay in the
                layout tree (React hides them with `display: none`, verified
                below), which is only affordable because the transcript is
                being windowed to its tail in parallel (#81) rather than kept
                whole forever.

                `display: none` already does the accessibility work: it drops
                the subtree from both the a11y tree and the tab order with no
                `aria-hidden`/`inert` needed, so none is added here — verified
                against a live React 19.2 build (Playwright: the hidden pane's
                content is absent from the accessibility snapshot, and `Tab`
                from the pane switcher lands on the document body, not on it).

                Crossing into/out of `split` still remounts both panes — see
                the branch above and the `showSplit` comment on `SPLIT_PANELS`
                for why that one is left alone. */}
            {transcriptMounted && (
              <Activity mode={paneView === "transcript" ? "visible" : "hidden"}>
                {transcript}
              </Activity>
            )}
            {workMounted && (
              <Activity mode={paneView === "work" ? "visible" : "hidden"}>
                {workPanel}
              </Activity>
            )}
          </div>
        )}
      </div>
    </Surface>
  );
}

/**
 * The workspace fills its shell slot exactly, never more.
 *
 * Deliberately NOT `absolute inset-0`. The shell's only `relative` ancestor is
 * the row that also contains the rail, so absolute positioning resolves against
 * the WHOLE viewport and the workspace paints over the sidebar — the header's
 * title landing on top of the brand. A plain flex child stays inside the
 * content column, and the bounded height it needs is already there: the shell
 * row is `h-dvh` and the slot is `h-full flex-1 overflow-hidden`, so `min-h-0`
 * here is what lets each pane below own its own scroll.
 */
function Surface({ children }: { children: React.ReactNode }) {
  return <div className="flex min-h-0 min-w-0 flex-1 flex-col">{children}</div>;
}

/**
 * Everything that used to be its own strip: what the agent is doing, the
 * interrupt, and which pane is showing.
 *
 * Interrupt lives here rather than in the composer because Grove steers a
 * WORKING agent: the composer must stay sendable while the agent runs, so the
 * stop control cannot be the composer's own send-button swap.
 */
function HeaderActions({
  view,
  onChange,
  splitOffered,
  status,
  canInterrupt,
  onInterrupt,
  isPublic,
}: {
  view: PaneView;
  onChange: (view: PaneView) => void;
  splitOffered: boolean;
  status: ReturnType<typeof findWorkspaceActivity>;
  canInterrupt: boolean;
  onInterrupt: () => void;
  isPublic: boolean;
}) {
  const live = status?.sessions[0]?.activity;
  const options: readonly PaneView[] = splitOffered
    ? ["transcript", "work", "split"]
    : ["transcript", "work"];

  return (
    <div className="flex min-w-0 items-center gap-2">
      {isPublic && (
        <Badge variant="secondary">
          <GlobeIcon aria-hidden />
          Public
        </Badge>
      )}
      {live && (
        <div className="hidden min-w-0 sm:block">
          <AgentStatus
            {...agentStatusProps(live, status?.phase ?? null, new Date())}
          />
        </div>
      )}
      {canInterrupt && (
        <Button
          size="xs"
          variant="outline"
          onClick={onInterrupt}
          data-testid="chat-interrupt"
        >
          <SquareIcon aria-hidden />
          Interrupt
        </Button>
      )}
      <Tabs value={view} onValueChange={(value) => onChange(value as PaneView)}>
        <TabsList variant="line" aria-label="Workspace panes">
          {options.map((option) => (
            <TabsTrigger
              key={option}
              value={option}
              data-testid={`pane-${option}`}
            >
              {LABELS[option]}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
    </div>
  );
}

const LABELS: Record<PaneView, string> = {
  transcript: "Transcript",
  work: "Work",
  split: "Split",
};
