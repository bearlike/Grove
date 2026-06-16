"use client";

import { useMemo } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { Composer } from "@/components/composer/composer";
import { WorkspaceGrid } from "@/components/workspace/workspace-grid";
import { FocusedPane } from "@/components/dashboard/focused-pane";
import { useActivityStream } from "@/lib/grove/hooks";
import { useUiStore } from "@/lib/grove/ui-store";

/**
 * The ONE workspace surface (#96 redesign) — composer-first. The hero is an
 * always-present composer that holds the visual center (Devin-style): type a
 * task, press Enter, a workspace is created. Below it, every workspace across
 * every project renders ONCE in the repo-grouped grid (the sidebar is nav/scope
 * only now — no more double-rendering). One live focused pane at a time, driven
 * by the store's `liveId`.
 *
 * Chrome (header + collapsible rail) is the shared `(shell)` layout; this page
 * renders the composer + grid. All view state (filters, scope, search, focus)
 * lives in the one client-state store; server state stays in TanStack Query.
 */
export default function HomePage() {
  const { snapshot, error } = useActivityStream();
  const liveId = useUiStore((s) => s.liveId);
  const clearLive = useUiStore((s) => s.clearLive);

  // Resolve the focused workspace from the FULL snapshot (independent of the
  // grid filter); drop the focus if it vanished or stopped working.
  const live = useMemo(() => {
    if (!liveId || !snapshot) return null;
    for (const p of snapshot.projects) {
      for (const w of p.workspaces) {
        if (w.state.id === liveId) {
          return w.sessions[0]?.activity.state === "working" ? w : null;
        }
      }
    }
    return null;
  }, [liveId, snapshot]);

  return (
    <div className="flex w-full flex-1 flex-col gap-5 p-4 pb-[env(safe-area-inset-bottom)]">
      {/* Hero: the brand mark anchoring the always-present composer. */}
      <div className="mx-auto flex w-full max-w-3xl flex-col items-center gap-3 pt-4 sm:pt-8">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src="/grove-logo.png"
          alt=""
          aria-hidden
          width={48}
          height={48}
          className="pointer-events-none size-12 opacity-90 select-none"
        />
        <Composer />
      </div>

      {error && !snapshot && (
        <div
          role="alert"
          className="rounded-md border border-[var(--status-error)] bg-[var(--status-error)]/10 p-4 text-sm"
        >
          Could not reach the daemon: {error.message}
        </div>
      )}

      {live && (
        <FocusedPane workspaceId={live.state.id} title={live.state.title} onClose={clearLive} />
      )}

      {!snapshot ? !error && <SkeletonGrid /> : <WorkspaceGrid snapshot={snapshot} />}
    </div>
  );
}

function SkeletonGrid() {
  return (
    <div className="flex flex-col gap-3">
      <Skeleton className="h-5 w-40" />
      <div className="grid grid-cols-[repeat(auto-fill,minmax(22rem,1fr))] gap-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-32 w-full" />
        ))}
      </div>
    </div>
  );
}
