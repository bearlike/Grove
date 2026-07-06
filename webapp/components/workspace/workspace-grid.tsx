"use client";
import { useMemo } from "react";
import { LayoutGrid } from "lucide-react";
import { ProjectSection } from "./project-section";
import { useUiStore } from "@/lib/grove/ui-store";
import { selectSections } from "@/lib/grove/dashboard-filter";
import type { DashboardSnapshotView } from "@/lib/grove/types";

/**
 * The repo-grouped workspace grid (issue #96 deliverables C/D). The ONLY public
 * prop is the server `snapshot`; every piece of VIEW intent is read straight off
 * the Zustand `ui-store` (scope · hidden states · attention-only · query · the
 * single live focus), so the page owns no grid policy and a sidebar filter flows
 * here without prop threading.
 *
 * Workspaces group into per-repo sections in snapshot order (the engine sorts
 * roots); within a section, `selectSections` sorts attention-first. A `scopeRepo`
 * narrows to one section. Empty sections drop out. The per-card ErrorBoundary
 * lives in `ProjectSection`.
 *
 * Test seam: `data-testid="workspace-grid"`; sections carry `project-section` +
 * `data-repo`, their headers `project-section-header`.
 */
export function WorkspaceGrid({ snapshot }: { snapshot: DashboardSnapshotView }) {
  const scopeRepo = useUiStore((s) => s.scopeRepo);
  const hiddenStates = useUiStore((s) => s.hiddenStates);
  const attentionOnly = useUiStore((s) => s.attentionOnly);
  const query = useUiStore((s) => s.query);
  const liveId = useUiStore((s) => s.liveId);
  const toggleLive = useUiStore((s) => s.toggleLive);

  const sections = useMemo(
    () => selectSections(snapshot, { scopeRepo, hiddenStates, attentionOnly, query }),
    [snapshot, scopeRepo, hiddenStates, attentionOnly, query],
  );

  if (sections.length === 0) {
    // Quiet icon + headline, not a bare muted sentence (design §5). The
    // test-pinned phrases ("No workspaces across any project" / "widen it")
    // stay verbatim in the supporting line.
    return (
      <div data-testid="workspace-grid" className="grid flex-1 place-items-center">
        <div className="flex flex-col items-center gap-2 p-6 text-center">
          <LayoutGrid aria-hidden className="size-6 text-muted-foreground/70" />
          {snapshot.total_workspaces === 0 ? (
            <>
              <h2 className="text-sm font-medium text-foreground">No workspaces yet</h2>
              <p className="max-w-xs text-sm text-muted-foreground">
                No workspaces across any project yet — describe a task in the composer to start one.
              </p>
            </>
          ) : (
            <>
              <h2 className="text-sm font-medium text-foreground">Nothing matches</h2>
              <p className="max-w-xs text-sm text-muted-foreground">
                No workspaces match the current filter — widen it in the sidebar.
              </p>
            </>
          )}
        </div>
      </div>
    );
  }

  return (
    <div data-testid="workspace-grid" className="flex flex-col gap-6">
      {sections.map((section) => (
        <ProjectSection
          key={section.repo_root}
          section={section}
          gridCols={GRID_COLS}
          liveId={liveId}
          onToggleLive={toggleLive}
        />
      ))}
    </div>
  );
}

// One source for the responsive grid geometry — auto-fill packs as many ≥22rem
// columns as the viewport holds; `auto-rows-fr` keeps a row's cards equal-height.
// 22rem holds the calm two-tier ADE card (header · happening line · one meta row)
// without the meta middots wrapping past two lines on a common branch/commit.
export const GRID_COLS = "grid auto-rows-fr grid-cols-[repeat(auto-fill,minmax(22rem,1fr))]";
