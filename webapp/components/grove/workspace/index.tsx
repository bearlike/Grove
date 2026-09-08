"use client";

import { Activity, useCallback, useEffect, useRef, useState } from "react";
import {
  Columns2Icon,
  GlobeIcon,
  MessageSquareTextIcon,
  PanelRightIcon,
  type LucideIcon,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useDefaultLayout, type LayoutStorage } from "react-resizable-panels";

import { ErrorState } from "@/components/elements/error-state";
import { useCloseAnnotationOnUnmount } from "@/components/grove/annotation";
import { ShellHeader } from "@/components/grove/shell/shell-header";
import { useSidebarUi } from "@/components/grove/shell/sidebar-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { SplitHandle } from "@/components/grove/split-handle";
import { ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { Tabs } from "@/components/ui/tabs";
import { AdaptiveTabsList, AdaptiveTabsTrigger } from "./adaptive-tabs";
import {
  findWorkspaceActivity,
  primarySessionId,
} from "@/lib/grove/adapters";
import { diagramOf } from "@/lib/grove/api";
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
  canInterruptNative,
  panesShown,
  storedView,
  visiblePane,
  resolvedWorkspaceSelection,
  type PanelTab,
  type PaneView,
  type WorkspaceSelection,
} from "./selectors";
import { useMinWidth } from "./use-min-width";
import { useWorkspaceOnboardingDemands } from "@/components/grove/onboarding";

const SPLIT_MIN_WIDTH = 1024;

/** The tour's asks this page owns; the composer takes `workspace-prompt` itself. */
const TOUR_KINDS = ["pane", "work-tab"] as const;

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
 * Which pane a workspace was last left on, keyed PER WORKSPACE ID — a bare
 * "last pane" key would apply one workspace's choice to a different one, which
 * is worse than always resetting to the default.
 *
 * The work TAB is deliberately absent from this. It used to be persisted
 * alongside the pane, which meant arriving at a workspace put you back on
 * whatever its terminal was doing days ago; every visit now lands on Info (see
 * `resolvedWorkspaceSelection`), and a tab clicked during a visit lives in
 * component state for that visit only.
 */
const viewStorageKey = (id: string) => `grove-workspace-view:${id}`;

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
  // A staged file belongs to this id's composer; an annotation pane open over
  // it has nowhere to save once the route leaves the id or unmounts.
  useCloseAnnotationOnUnmount(id);
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
    const stored = storedView(readPaneStorage(viewStorageKey(id)));
    setSelection((current) => {
      if (selectionWorkspaceId.current !== id) return current;
      return { ...current, view: current.view ?? stored };
    });
  }, [id]);

  // Visit-scoped, never written to storage: see `viewStorageKey`.
  const selectWorkTab = useCallback((tab: PanelTab) => {
    setSelection((current) => ({ ...current, workTab: tab }));
  }, []);

  // A diagram opening is the ONE event that takes the work tab without being
  // clicked, and it does so exactly once per collaboration identity.
  //
  // `undefined` is "not observed yet" and is deliberately distinct from `null`:
  // the FIRST value this page sees is recorded and never acted on, because on a
  // fresh load a descriptor that was already there says nothing about now. Only
  // a change observed live (none → open, or a reopen minting a new id) is the
  // agent actually opening a diagram, and a reader who moves to Terminal
  // afterwards is never dragged back.
  //
  // THE FIRST VALUE IS THE ONE THE PEEK ANSWERED WITH, never the gap before it.
  // `peek` is a query, so an unresolved read looks exactly like "no diagram" —
  // which turned every fresh load of a workspace that already had one into an
  // apparent `null` → id open, and landed the reader on Diagram. The whole
  // guard was defeated by a loading state. Measured on the built app: a
  // workspace with an open diagram selected Diagram on every reload.
  const diagramSessionId = peek.data
    ? (diagramOf(peek.data.state)?.session_id ?? null)
    : undefined;
  const announcedDiagram = useRef<string | null | undefined>(undefined);
  useEffect(() => {
    if (diagramSessionId === undefined) return;
    const seen = announcedDiagram.current;
    announcedDiagram.current = diagramSessionId;
    if (seen === undefined || seen === diagramSessionId || diagramSessionId === null) return;
    selectWorkTab("diagram");
  }, [diagramSessionId, selectWorkTab]);

  const changeView = (next: PaneView) => {
    setSelection((current) => ({ ...current, view: next }));
    writePaneStorage(viewStorageKey(id), next);
  };

  // The onboarding tour selects a pane and a work tab the way a click would.
  // A `split` ask on a narrow window falls through `visiblePane` like any
  // stored split does; the tab it names is still selected underneath.
  useWorkspaceOnboardingDemands(
    TOUR_KINDS,
    useCallback(
      (demand) => {
        if (demand.kind === "pane") {
          setSelection((current) => ({ ...current, view: demand.view }));
          return;
        }
        selectWorkTab(demand.tab);
        setSelection((current) => ({ ...current, view: current.view ?? "split" }));
      },
      [selectWorkTab],
    ),
  );

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
      native={peek.data.state.native}
    />
  );
  const workPanel = (
    <WorkPanel
      peek={peek.data}
      activity={activity}
      commits={commits.data}
      repoRoot={peek.data.state.repo_root}
      privileged={{
        peek: peek.data,
        onKilled: () => router.push("/"),
        canInterrupt: peek.data.state.native ? canInterruptNative(peek.data.state) : thread.working,
        onExpandDiagram: () => {
          selectWorkTab("diagram");
          changeView("work");
          useSidebarUi.getState().setCollapsed(true);
          useSidebarUi.getState().setMobileOpen(false);
        },
      }}
      tab={workTab}
      onTabChange={selectWorkTab}
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
            <SplitHandle />
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
 * WHICH PANE IS SHOWING. Nothing else.
 *
 * IT NAVIGATES; it does not act, and it no longer reports. Send keys used to
 * sit here, which made this strip one verb wide and put a control that talks to
 * a terminal beside the control that chooses a pane; it is a card on Controls
 * now. The agent's status pill followed it out, for the neighbouring reason:
 * a scrolling sentence of the agent's own prose, sitting one gap from the pane
 * tabs, was the widest and most restless thing in a 32px band whose whole job
 * is to stay still and let you aim at it. The claim itself is not lost — the
 * same `PhaseView` note renders on the Task card, where a reader who came to
 * read a status can read it without it moving.
 */
function HeaderActions({
  view,
  onChange,
  splitOffered,
  isPublic,
}: {
  view: PaneView;
  onChange: (view: PaneView) => void;
  splitOffered: boolean;
  isPublic: boolean;
}) {
  const options: readonly PaneView[] = splitOffered
    ? ["transcript", "work", "split"]
    : ["transcript", "work"];

  return (
    <div className="workspace-pane-actions flex min-w-0 flex-1 items-center gap-2 self-stretch">
      {isPublic && (
        <Badge variant="secondary" className="shrink-0">
          <GlobeIcon aria-hidden />
          Public
        </Badge>
      )}
      <Tabs className="min-w-0 flex-1 self-stretch" value={view} onValueChange={(value) => onChange(value as PaneView)}>
        <AdaptiveTabsList className="h-full" aria-label="Workspace panes">
          {options.map((option) => {
            const { label, Icon } = PANE_CHROME[option];
            return (
              <AdaptiveTabsTrigger
                key={option}
                value={option}
                label={label}
                icon={Icon}
                data-testid={`pane-${option}`}
              />
            );
          })}
        </AdaptiveTabsList>
      </Tabs>
    </div>
  );
}

const PANE_CHROME: Record<PaneView, { label: string; Icon: LucideIcon }> = {
  transcript: { label: "Transcript", Icon: MessageSquareTextIcon },
  work: { label: "Work", Icon: PanelRightIcon },
  split: { label: "Split", Icon: Columns2Icon },
};
