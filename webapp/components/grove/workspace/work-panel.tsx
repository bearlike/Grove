"use client";

import dynamic from "next/dynamic";
import {
  FileDiffIcon,
  GitCompareArrowsIcon,
  InfoIcon,
  PanelTopIcon,
  RadioIcon,
  SlidersHorizontalIcon,
  TerminalIcon,
  WorkflowIcon,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { Tabs, TabsContent } from "@/components/ui/tabs";
import { AdaptiveTabsList, AdaptiveTabsTrigger } from "./adaptive-tabs";
import { Skeleton } from "@/components/ui/skeleton";
import { diagramOf } from "@/lib/grove/api";
import type { CommitSummaryView, WorkspacePeekView } from "@/lib/grove/api";
import { useWorkspacePanels } from "@/lib/grove/hooks";
import {
  offeredPanelTabs,
  PANEL_TAB_VALUES,
  panelTabValue,
  type ActivityRead,
  type PanelTab,
  type WorkspaceRead,
} from "./selectors";

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
const DiagramTab = dynamic(() => import("./diagram-tab").then((m) => m.DiagramTab), {
  loading: TabFallback,
});
const EmbeddedPanelTab = dynamic(
  () => import("./panel-tab").then((m) => m.PanelTab),
  { loading: TabFallback },
);

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
 * The work row owns its closing rule; the pane switcher instead shares the
 * shell header's rule. Both compose AdaptiveTabsList so labels yield to this
 * pane's width, while the native trigger owns its highlight through resizing.
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
   * needs the pane, Files needs a workspace id to fetch privately, and Controls
   * needs all three of these — a record carrying `branch_provenance` for the
   * lifecycle verbs, a kill handler, and whether there is a turn to cancel.
   * Bundling them means the panel's own reach is DERIVED from what it was
   * handed, so a caller cannot ask for a tab whose data it did not provide, and
   * nobody can leave a boolean disagreeing with a handler.
   *
   * `canInterrupt` rides along rather than becoming a fourth top-level prop for
   * the same reason: it is only ever true for a caller holding a live thread,
   * and the public view has no turn to cancel.
   *
   * Withholding these is chrome, never the security boundary: the public
   * surface is safe because the daemon serves it three read-only routes, not
   * because a trigger is missing. If the only thing stopping a reader were this
   * prop, the feature would be broken.
   */
  privileged?: {
    peek: WorkspacePeekView;
    onKilled: () => void;
    canInterrupt: boolean;
    onExpandDiagram?: () => void;
  };
}) {
  const panels = useWorkspacePanels(privileged?.peek.state.id ?? null);
  const diagram = privileged ? diagramOf(privileged.peek.state) : null;
  // Order is the census's, never the caller's — a subset cannot reorder the
  // strip, and a seventh tab lands in the right place for both audiences at once.
  const offered = offeredPanelTabs(privileged !== undefined, diagram !== null);
  const panelTabs = privileged ? (panels.data ?? []) : [];
  // Falls back against what is OFFERED, not against the whole census: Diagram
  // is conditional, so a workspace whose diagram was closed while its tab was
  // selected must land somewhere real rather than on an empty panel.
  const activeTab = panelTabs.some((panel) => panelTabValue(panel.name) === tab)
    ? tab
    : (offered as readonly PanelTab[]).includes(tab)
      ? tab
      : privileged
        ? "terminal"
        : "info";
  return (
    <Tabs
      value={activeTab}
      onValueChange={(value) => onTabChange(value as PanelTab)}
      className="flex min-h-0 min-w-0 flex-1 flex-col gap-0 bg-background"
      data-testid="work-panel"
    >
      <AdaptiveTabsList className="workspace-work-tabs shrink-0" aria-label="Workspace tools">
        {offered.map((value) => {
          const { label, Icon } = tabChrome(value, privileged?.peek.state.native ?? false);
          return (
            <AdaptiveTabsTrigger
              key={value}
              value={value}
              label={label}
              icon={Icon}
              data-testid={`work-panel-tab-${value}`}
            />
          );
        })}
        {panelTabs.map((panel) => (
          <AdaptiveTabsTrigger
            key={panel.name}
            value={panelTabValue(panel.name)}
            label={panel.title}
            icon={PanelTopIcon}
            data-testid={`work-panel-tab-panel-${panel.name}`}
          />
        ))}
      </AdaptiveTabsList>

      {/* Empty-state navigation uses the controlled tab setter: a fragment
          anchor cannot select an inactive Radix tab. Standalone callers may
          omit it, in which case no navigation action renders. */}
      <TabsContent value="changes" className="flex min-h-0 flex-1 flex-col">
        <ChangesTab peek={peek} commits={commits} onNavigate={onTabChange} />
      </TabsContent>
      <TabsContent value="info" className="flex min-h-0 flex-1 flex-col">
        <InfoTab
          peek={peek}
          activity={activity}
          repoRoot={repoRoot}
          identity={privileged?.peek.state}
          onNavigate={onTabChange}
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
            <FilesTab workspaceId={privileged.peek.state.id} onNavigate={onTabChange} />
          </TabsContent>
          <TabsContent value="controls" className="flex min-h-0 flex-1 flex-col">
            <ControlsTab
              state={privileged.peek.state}
              onKilled={privileged.onKilled}
              canInterrupt={privileged.canInterrupt}
            />
          </TabsContent>
          {/* Keep editable iframe drafts alive, not visible. Radix forceMount
              makes Presence true even for an inactive tab, so its built-in
              hidden flag stays false. Explicit hiding must own layout; inert
              only owns interaction. Read-only diagrams have no live edit to
              preserve and use ordinary mount-on-selection behavior. */}
          {diagram && (
            <TabsContent
              value="diagram"
              forceMount={diagram.mode === "active" ? true : undefined}
              hidden={activeTab !== "diagram"}
              inert={activeTab !== "diagram" ? true : undefined}
              className="min-h-0 flex-1 overflow-hidden"
            >
              <div className="flex h-full min-h-0 min-w-0 flex-col">
                <DiagramTab
                  workspaceId={privileged.peek.state.id}
                  repoRoot={privileged.peek.state.repo_root}
                  descriptor={diagram}
                  active={activeTab === "diagram"}
                  onExpand={privileged.onExpandDiagram}
                />
              </div>
            </TabsContent>
          )}
          {panelTabs.map((panel) => (
            <TabsContent
              key={panel.name}
              value={panelTabValue(panel.name)}
              className="flex min-h-0 flex-1 flex-col"
            >
              <EmbeddedPanelTab url={panel.url} title={panel.title} />
            </TabsContent>
          ))}
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
  diagram: { label: "Diagram", Icon: WorkflowIcon },
  files: { label: "Files", Icon: FileDiffIcon },
  info: { label: "Info", Icon: InfoIcon },
  controls: { label: "Controls", Icon: SlidersHorizontalIcon },
};

/**
 * The pane tab is named for what it SHOWS. A native workspace's pane is
 * Grove's own worker printing the session's protocol frames, one line each,
 * so calling that surface "Terminal" promises a place to type that does not
 * exist; it is the session's event stream. Same tab value, same capture
 * path, one label — the value stays `terminal` so a bookmarked tab and the
 * e2e census survive either mode.
 */
export const STREAM_TAB_CHROME = { label: "Stream", Icon: RadioIcon } as const;

export function tabChrome(value: PanelTab, native: boolean): { label: string; Icon: LucideIcon } {
  return value === "terminal" && native ? STREAM_TAB_CHROME : TAB_CHROME[value];
}

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
