"use client";

import { useEffect, useMemo, useState } from "react";
import { useAutoAnimate } from "@formkit/auto-animate/react";
import { Header } from "@/components/layout/header";
import { Skeleton } from "@/components/ui/skeleton";
import { SessionCard } from "@/components/dashboard/session-card";
import { FocusedPane } from "@/components/dashboard/focused-pane";
import { DashboardFilter } from "@/components/dashboard/dashboard-filter";
import { RefreshButton } from "@/components/dashboard/refresh-button";
import { ErrorBoundary } from "@/components/error-boundary";
import { RelativeTime } from "@/components/shared/relative-time";
import { useActivityStream } from "@/lib/grove/hooks";
import {
  computeFacets,
  emptyFilter,
  filterSnapshot,
  sortSnapshotByActivity,
  type DashboardFilterState,
} from "@/lib/grove/dashboard-filter";
import { loadFilter, saveFilter } from "@/lib/grove/filter-persistence";
import type { DashboardSnapshotView } from "@/lib/grove/types";

/**
 * The cross-project Activity Dashboard — every agent session across every repo,
 * grouped by project, streamed live from the daemon over SSE (with a `/activity`
 * poll fallback). One consolidated filter (projects × agent-states × attention,
 * with live counts) narrows the wall; the single focused pane shows one live
 * terminal at a time.
 */
export default function ActivityPage() {
  const { snapshot, connected, lastEventAt, refresh } = useActivityStream();
  // Start from `emptyFilter()` on BOTH server and first client render so the
  // markup matches (no hydration mismatch — localStorage is client-only); then
  // hydrate the persisted filter in an effect, after which we own + persist it.
  const [filter, setFilter] = useState<DashboardFilterState>(emptyFilter);
  const [hydrated, setHydrated] = useState(false);
  useEffect(() => {
    setFilter(loadFilter());
    setHydrated(true);
  }, []);
  useEffect(() => {
    // Persist only once we've hydrated, so the initial empty default never
    // clobbers a saved filter before the load effect runs.
    if (hydrated) saveFilter(filter);
  }, [filter, hydrated]);
  // The single focused live pane (the "one live focus" shape — never N panes).
  const [liveId, setLiveId] = useState<string | null>(null);

  // Resolve the focused workspace from the FULL snapshot (independent of the
  // wall filter); drop the focus if it vanished or stopped working.
  const live = useMemo(() => {
    if (!liveId || !snapshot) return null;
    for (const w of snapshot.projects.flatMap((p) => p.workspaces)) {
      if (w.state.id === liveId) {
        return w.sessions[0]?.activity.state === "working" ? w : null;
      }
    }
    return null;
  }, [liveId, snapshot]);

  const toggleLive = (id: string) => setLiveId((cur) => (cur === id ? null : id));
  const facets = useMemo(() => (snapshot ? computeFacets(snapshot) : null), [snapshot]);

  return (
    <>
      <Header />
      <main className="mx-auto flex w-full max-w-screen-2xl flex-1 flex-col gap-3 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-baseline gap-2">
            <h1 className="text-xl font-semibold tracking-tight">Activity</h1>
            {facets && (
              <span className="text-xs text-muted-foreground">
                {facets.total} session{facets.total === 1 ? "" : "s"}
                {facets.attention > 0 && (
                  <>
                    {" · "}
                    <span className="text-[var(--status-orphaned)]">
                      {facets.attention} need attention
                    </span>
                  </>
                )}
              </span>
            )}
            <span
              data-testid="stream-status"
              data-connected={connected}
              className="text-xs text-muted-foreground"
              aria-live="polite"
            >
              {connected ? "live" : "polling"}
              {lastEventAt !== null && (
                <>
                  {" · updated "}
                  <RelativeTime iso={new Date(lastEventAt).toISOString()} />
                </>
              )}
            </span>
            <RefreshButton onRefresh={refresh} />
          </div>
          {facets && <DashboardFilter facets={facets} value={filter} onChange={setFilter} />}
        </div>

        {live && (
          <FocusedPane
            workspaceId={live.state.id}
            title={live.state.title}
            onClose={() => setLiveId(null)}
          />
        )}

        {!snapshot ? (
          <SkeletonGrid />
        ) : (
          <ActivityWall
            snapshot={snapshot}
            filter={filter}
            liveId={liveId}
            onToggleLive={toggleLive}
          />
        )}
      </main>
    </>
  );
}

function ActivityWall({
  snapshot,
  filter,
  liveId,
  onToggleLive,
}: {
  snapshot: DashboardSnapshotView;
  filter: DashboardFilterState;
  liveId: string | null;
  onToggleLive: (id: string) => void;
}) {
  // Filter, then float active sessions (and the projects that hold them) to the
  // front. auto-animate on the containers tweens the resulting reorder.
  const groups = sortSnapshotByActivity(filterSnapshot(snapshot, filter));
  // auto-animate honors `prefers-reduced-motion` internally (no-op tween there).
  const [wallRef] = useAutoAnimate<HTMLDivElement>();

  if (groups.length === 0) {
    return (
      <div className="grid flex-1 place-items-center text-sm text-muted-foreground">
        {snapshot.total_workspaces === 0
          ? "No workspaces across any project yet."
          : "No sessions match the current filter — open Filter to widen it."}
      </div>
    );
  }

  return (
    <div
      ref={wallRef}
      className="flex flex-1 flex-col gap-5 overflow-auto"
      data-testid="activity-wall"
    >
      {groups.map((group) => (
        <ProjectGroupSection
          key={group.repo_root}
          group={group}
          liveId={liveId}
          onToggleLive={onToggleLive}
        />
      ))}
    </div>
  );
}

function ProjectGroupSection({
  group,
  liveId,
  onToggleLive,
}: {
  group: ReturnType<typeof filterSnapshot>[number];
  liveId: string | null;
  onToggleLive: (id: string) => void;
}) {
  // Per-group grid gets its own auto-animate ref so card reorders within a
  // project tween independently of project-group reordering above.
  const [gridRef] = useAutoAnimate<HTMLDivElement>();
  return (
    <section>
      <h2 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
        {group.repo_name}{" "}
        <span className="text-muted-foreground/60">({group.workspaces.length})</span>
      </h2>
      <div ref={gridRef} className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
        {group.workspaces.map((w) => (
          // One boundary per card: a single malformed row degrades to a
          // placeholder tile instead of unmounting the whole wall.
          <ErrorBoundary key={w.state.id} fallback={<CardErrorTile title={w.state.title} />}>
            <SessionCard
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

function SkeletonGrid() {
  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
      {Array.from({ length: 6 }).map((_, i) => (
        <Skeleton key={i} className="h-44 w-full" />
      ))}
    </div>
  );
}
