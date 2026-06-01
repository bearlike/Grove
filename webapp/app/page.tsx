"use client";

import { useMemo } from "react";
import { Header } from "@/components/layout/header";
import { Skeleton } from "@/components/ui/skeleton";
import { RepoFacetTabs } from "@/components/workspace/repo-facet-tabs";
import { useWorkspaces, useWorkspacesPeeks } from "@/lib/grove/hooks";

export default function HomePage() {
  const { data, isLoading, isError, error } = useWorkspaces();
  // Stable id list so `useQueries` keys don't churn on every render.
  const ids = useMemo(() => (data ?? []).map((w) => w.id), [data]);
  const peeks = useWorkspacesPeeks(ids);

  return (
    <>
      <Header />
      <main className="mx-auto w-full max-w-screen-xl p-4 pb-[env(safe-area-inset-bottom)]">
        {isLoading && <SkeletonGrid />}
        {isError && (
          <div
            role="alert"
            className="rounded-md border border-[var(--status-error)] bg-[var(--status-error)]/10 p-4 text-sm"
          >
            Could not reach the daemon: {String((error as Error).message)}
          </div>
        )}
        {data && <RepoFacetTabs workspaces={data} peeks={peeks} />}
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
