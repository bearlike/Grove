"use client";

import { useMemo } from "react";
import { Header } from "@/components/layout/header";
import { Skeleton } from "@/components/ui/skeleton";
import { RepoFacetTabs } from "@/components/workspace/repo-facet-tabs";
import { useActivityStream } from "@/lib/grove/hooks";

export default function HomePage() {
  // One stream feeds the whole grid: SSE deltas land in <1 s and a new
  // workspace appears on the next snapshot — no per-card peek fan-out.
  const { snapshot, error } = useActivityStream();
  const activities = useMemo(
    () => snapshot?.projects.flatMap((p) => p.workspaces) ?? null,
    [snapshot],
  );

  return (
    <>
      <Header />
      <main className="mx-auto w-full max-w-screen-xl p-4 pb-[env(safe-area-inset-bottom)]">
        {!snapshot && !error && <SkeletonGrid />}
        {error && (
          <div
            role="alert"
            className="rounded-md border border-[var(--status-error)] bg-[var(--status-error)]/10 p-4 text-sm"
          >
            Could not reach the daemon: {error.message}
          </div>
        )}
        {activities && <RepoFacetTabs activities={activities} />}
      </main>
    </>
  );
}

function SkeletonGrid() {
  return (
    <div className="grid auto-rows-fr grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {Array.from({ length: 6 }).map((_, i) => (
        <Skeleton key={i} className="h-40 w-full" />
      ))}
    </div>
  );
}
