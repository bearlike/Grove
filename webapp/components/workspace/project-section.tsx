"use client";
import { useAutoAnimate } from "@formkit/auto-animate/react";
import { WorkspaceCard } from "./card";
import { ErrorBoundary } from "@/components/error-boundary";
import { cn } from "@/lib/utils";
import type { RepoSection } from "@/lib/grove/dashboard-filter";

/**
 * One repo band of the grid — repo-grouped sections.
 * A LIGHT section header (repo name + count) labels the band; its cards pack
 * attention-first into the responsive track. Repo identity lives HERE now, so
 * the cards inside drop their old project chip.
 *
 * Test seam: `data-testid="project-section"` + `data-repo` on the band, the
 * header on `data-testid="project-section-header"`.
 */
export function ProjectSection({
  section,
  gridCols,
  liveId,
  onToggleLive,
}: {
  section: RepoSection;
  gridCols: string;
  liveId: string | null;
  onToggleLive: (id: string) => void;
}) {
  // One auto-animate ref per section tweens its SSE-driven reorders (it honors
  // `prefers-reduced-motion` internally — a no-op tween there).
  const [gridRef] = useAutoAnimate<HTMLDivElement>();
  return (
    <section data-testid="project-section" data-repo={section.repo_root} className="flex flex-col gap-2">
      <h2
        data-testid="project-section-header"
        className="flex items-baseline gap-2 px-2.5 text-xs font-medium text-muted-foreground"
      >
        <span className="truncate">{section.repo_name}</span>
        <span className="tabular-nums text-muted-foreground/60">{section.workspaces.length}</span>
      </h2>
      <div ref={gridRef} className={cn(gridCols, "content-start gap-3")}>
        {section.workspaces.map((w) => (
          // One boundary per card: a single malformed row degrades to a
          // placeholder tile instead of unmounting the whole grid.
          <ErrorBoundary key={w.state.id} fallback={<CardErrorTile title={w.state.title} />}>
            <WorkspaceCard
              activity={w}
              liveOpen={w.state.id === liveId}
              onToggleLive={onToggleLive}
            />
          </ErrorBoundary>
        ))}
      </div>
    </section>
  );
}

function CardErrorTile({ title }: { title: string }) {
  // The per-card fallback — keeps the grid cell occupied and names the
  // workspace so a contained failure is legible, not a silent gap.
  return (
    <div
      data-testid="card-error"
      role="alert"
      className="rounded-lg border border-[var(--status-error)]/40 bg-card p-4"
    >
      <p className="truncate text-base font-semibold">{title}</p>
      <p className="mt-1 text-xs text-muted-foreground">
        <span className="font-mono text-[var(--status-error)]">render error</span> — couldn&apos;t
        draw this card from the current data.
      </p>
    </div>
  );
}
