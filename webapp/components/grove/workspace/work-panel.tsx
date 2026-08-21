"use client";

import dynamic from "next/dynamic";
import {
  FileDiffIcon,
  GitCompareArrowsIcon,
  InfoIcon,
  SlidersHorizontalIcon,
  TerminalIcon,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/assistant-ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import type { CommitSummaryView, WorkspacePeekView } from "@/lib/grove/api";
import {
  PANEL_TAB_VALUES,
  type ActivityRead,
  type PanelTab,
  type WorkspaceRead,
} from "./selectors";

/**
 * The tabs a reader with no Grove session may see: what the work IS, and what
 * it CHANGED. Terminal, Files and Controls are absent because each is either a
 * live handle on the machine or a way to steer the agent.
 *
 * A subset of `PANEL_TAB_VALUES` rather than a parallel list, so a name that
 * does not exist in the census cannot be written here.
 */
const SHARED_TABS: readonly PanelTab[] = ["changes", "info"];

/**
 * Each tab is its own chunk, fetched only once its `TabsContent` actually
 * mounts — not a static `import { X } from "./x-tab"` that lands in this
 * module's OWN chunk regardless of which tab is selected. `Tabs` already
 * unmounts every tab but the active one (see the doc comment on
 * `WorkPanel` below); a static import doesn't know that at build time, so
 * all five tabs' dependencies used to ship on every workspace page load —
 * four tabs' worth nobody had opened yet. This moves WHEN each tab's code
 * downloads, not how big any one of them is.
 *
 * `next/dynamic`'s `loading` slot (not `React.lazy` + a hand-rolled
 * `Suspense`, the choice `code-block.tsx` had to make): no tab needs its
 * own props inside its fallback, so the built-in primitive is the right
 * level here. `ssr` is left at its default (`true`) deliberately: the
 * initially-selected tab is real content on first paint, not something
 * gated behind a click the way a collapsed code block or diff row is, so
 * forcing `ssr: false` would trade a real server-rendered first tab for a
 * guaranteed skeleton flash on every page load.
 */
const TerminalTab = dynamic(() => import("./terminal-tab").then((m) => m.TerminalTab), {
  loading: TabFallback,
});
const ChangesTab = dynamic(() => import("./changes-tab").then((m) => m.ChangesTab), {
  loading: TabFallback,
});
const FilesTab = dynamic(() => import("./files-tab").then((m) => m.FilesTab), {
  loading: TabFallback,
});
const InfoTab = dynamic(() => import("./info-tab").then((m) => m.InfoTab), {
  loading: TabFallback,
});
const ControlsTab = dynamic(() => import("./controls-tab").then((m) => m.ControlsTab), {
  loading: TabFallback,
});

/**
 * The tabbed half of the workspace surface — everything about the work that is
 * not the conversation.
 *
 * Only the selected tab mounts, which is what keeps the terminal's SSE stream
 * closed while you are reading the diff.
 *
 * Tab choice is CALLER state, not local state: below the split breakpoint this
 * component sits directly under the page; above it, `Workspace` mounts it one
 * level deeper inside a `ResizablePanelGroup`. That tree-shape change unmounts
 * and remounts this component on every breakpoint crossing, so a local
 * `useState` here would reset to "terminal" the instant the layout collapsed —
 * silently, before any click. Holding the value in `Workspace` instead means
 * the remount just re-reads the same prop; the choice survives the round trip.
 *
 * The panel is a BOUNDED column: it fills whatever height its parent gives it,
 * paints a solid background over it, and every tab scrolls inside that box.
 * `min-h-0` on the root and on each `TabsContent` is what enforces it — a flex
 * child without it refuses to shrink below its content, and the panel's scroll
 * silently becomes the page's.
 *
 * `pt-4` IS THE ONLY THING SEPARATING TWO ROWS OF CONTROLS, so it is not
 * decoration. `ShellHeader` is `h-12` with deliberately NO `border-b` — a rule
 * under it would read as a second piece of chrome sitting on the page — and it
 * carries its own tab strip (the pane switcher) in `actions`. So this strip
 * used to begin at the exact pixel that one ended: two tab lists, touching,
 * with nothing saying they belong to different layers.
 *
 * Space is therefore the whole mechanism, and the amount has to beat the
 * spacing INSIDE either row or it reads as more of the same row: 16px is twice
 * the strip's own `pb-2` between its labels and their underline, and twice the
 * header's `gap-2` between its controls. The border the `line` variant already
 * draws under this strip then closes the band from below.
 *
 * It lives here rather than on `ShellHeader` because the header is shared by
 * every page and only this one stacks a second control row beneath it — and
 * because the split view mounts THIS component inside a `ResizablePanel`, so
 * putting the clearance on the panel is what makes Work-alone and Split
 * identical by construction instead of by two matching numbers.
 */
export function WorkPanel({
  peek,
  activity,
  commits,
  tab,
  onTabChange,
  repoRoot,
  privileged,
}: {
  peek: WorkspaceRead;
  activity: ActivityRead | null;
  commits: CommitSummaryView[] | undefined;
  tab: PanelTab;
  onTabChange: (tab: PanelTab) => void;
  repoRoot: string | null;
  /**
   * Everything only a caller with a Grove session can supply — and therefore
   * everything only such a caller may see.
   *
   * ONE optional prop rather than a `tabs` array beside an `onKilled` beside a
   * `readOnly`, because the tab set is not an independent choice: Terminal
   * needs the pane, Files and Controls need a workspace id to fetch privately,
   * and Lifecycle needs a record carrying `branch_provenance`. Bundling them
   * means the panel's own reach is DERIVED from what it was handed, so a caller
   * cannot ask for a tab whose data it did not provide, and nobody can leave a
   * boolean disagreeing with a handler.
   *
   * Withholding these is chrome, never the security boundary: the public
   * surface is safe because the daemon serves it three read-only routes, not
   * because a trigger is missing. If the only thing stopping a reader were this
   * prop, the feature would be broken.
   */
  privileged?: {
    peek: WorkspacePeekView;
    onKilled: () => void;
  };
}) {
  // Order is the census's, never the caller's — a subset cannot reorder the
  // strip, and a sixth tab lands in the right place for both audiences at once.
  const offered = PANEL_TAB_VALUES.filter((value) => privileged || SHARED_TABS.includes(value));
  return (
    <Tabs
      value={tab}
      onValueChange={(value) => onTabChange(value as PanelTab)}
      className="flex min-h-0 min-w-0 flex-1 flex-col gap-0 bg-background pt-4"
      data-testid="work-panel"
    >
      <TabsList
        variant="line"
        size="sm"
        className="w-full shrink-0 justify-start overflow-x-auto px-3"
      >
        {offered.map((value) => {
          const { label, Icon } = TAB_CHROME[value];
          return (
            <TabsTrigger key={value} value={value} data-testid={`work-panel-tab-${value}`}>
              <Icon aria-hidden />
              {label}
            </TabsTrigger>
          );
        })}
      </TabsList>

      <TabsContent value="changes" className="flex min-h-0 flex-1 flex-col">
        <ChangesTab peek={peek} commits={commits} />
      </TabsContent>
      <TabsContent value="info" className="flex min-h-0 flex-1 flex-col">
        <InfoTab
          peek={peek}
          activity={activity}
          repoRoot={repoRoot}
          privileged={
            privileged && {
              state: privileged.peek.state,
              onKilled: privileged.onKilled,
            }
          }
        />
      </TabsContent>
      {/* The three privileged tabs are not merely untriggerable without
          `privileged` — they are not in the tree at all. A hidden `TabsContent`
          still mounts nothing here (see the doc comment above on why only the
          active tab mounts), but leaving them declared would put three
          components that dereference a full peek behind a value that may not
          exist, which is a compile error waiting for whoever adds a fourth. */}
      {privileged && (
        <>
          <TabsContent value="terminal" className="flex min-h-0 flex-1 flex-col">
            <TerminalTab peek={privileged.peek} active={tab === "terminal"} />
          </TabsContent>
          <TabsContent value="files" className="flex min-h-0 flex-1 flex-col">
            <FilesTab workspaceId={privileged.peek.state.id} />
          </TabsContent>
          <TabsContent value="controls" className="flex min-h-0 flex-1 flex-col">
            <ControlsTab workspaceId={privileged.peek.state.id} />
          </TabsContent>
        </>
      )}
    </Tabs>
  );
}

/**
 * How each tab presents itself. A `Record` over the union, NOT a second list of
 * tab names: the order and the census both live in `PANEL_TAB_VALUES`
 * (`./selectors`), so a sixth tab added there without an entry here fails to
 * compile rather than silently missing from the strip. The same "total by
 * construction" shape `selectors.ts` already uses for its ticket-state maps.
 */
const TAB_CHROME: Record<PanelTab, { label: string; Icon: LucideIcon }> = {
  terminal: { label: "Terminal", Icon: TerminalIcon },
  changes: { label: "Changes", Icon: GitCompareArrowsIcon },
  files: { label: "Files", Icon: FileDiffIcon },
  info: { label: "Info", Icon: InfoIcon },
  controls: { label: "Controls", Icon: SlidersHorizontalIcon },
};

/**
 * Stand-in for whichever tab is still downloading its own chunk — never an
 * empty box. Echoes the header-band-plus-rows anatomy every real tab uses
 * (`CardGrid`/`SectionCard`) closely enough that the swap to real content
 * doesn't read as a different surface, though the exact height still moves
 * once real data lands: five different tabs cannot share one true skeleton
 * without importing each tab's own (which would defeat the split).
 */
function TabFallback() {
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 p-4" aria-hidden>
      <Skeleton className="h-4 w-40" />
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-24 w-full" />
    </div>
  );
}
