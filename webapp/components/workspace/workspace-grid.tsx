"use client";
import { useMemo } from "react";
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
    return (
      <div
        data-testid="workspace-grid"
        className="grid flex-1 place-items-center text-sm text-muted-foreground"
      >
        {snapshot.total_workspaces === 0
          ? "No workspaces across any project yet."
          : "No workspaces match the current filter — widen it in the sidebar."}
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
// 22rem (was 18rem) gives the restructured three-region card room to breathe so
// the title, two-line task, branch sub-row, and footer well never crowd.
export const GRID_COLS = "grid auto-rows-fr grid-cols-[repeat(auto-fill,minmax(22rem,1fr))]";
