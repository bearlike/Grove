"use client";

import { useMemo } from "react";
import Link from "next/link";
import { LayoutGrid, MessageSquarePlus } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { Composer } from "@/components/composer/composer";
import { WorkspaceGrid } from "@/components/workspace/workspace-grid";
import { FocusedPane } from "@/components/dashboard/focused-pane";
import { useActivityStream } from "@/lib/grove/hooks";
import { useUiStore, type LandingView } from "@/lib/grove/ui-store";
import { cn } from "@/lib/utils";
import type { DashboardSnapshotView } from "@/lib/grove/types";

/**
 * The landing surface (ADE #140, design §4.1). A persisted `Hero | Overview`
 * toggle reconciles the familiar-chat vs power-fleet tension (ruling 1.1):
 *
 *  - **Hero** (default; clean first-run + demos): the always-present
 *    composer-hero — type a task, press Enter, a workspace is born — plus a
 *    quiet "Recent" strip. The fleet is never hidden: it lives in the left
 *    session rail one glance away.
 *  - **Overview**: today's rich repo-grouped card grid VERBATIM (cards + the
 *    Live focused pane untouched — zero-functionality-loss, D2), for the
 *    power-user who wants the whole wall at once.
 *
 * The choice rides ONE persisted `ui-store` slice (`landingView`) alongside
 * `sidebarCollapsed` — zero new machinery, mechanism-not-policy applied to
 * Grove's own landing. Hero renders on SSR + first paint (matching the persisted
 * default) until `hydrated` flips to the stored choice, the same mount-guard the
 * sidebar collapse uses so the markup never mismatches.
 */
export default function HomePage() {
  const { snapshot, error } = useActivityStream();
  const liveId = useUiStore((s) => s.liveId);
  const clearLive = useUiStore((s) => s.clearLive);
  const landingView = useUiStore((s) => s.landingView);
  const setLandingView = useUiStore((s) => s.setLandingView);
  const hydrated = useUiStore((s) => s.hydrated);
  const view: LandingView = hydrated ? landingView : "hero";

  // Resolve the focused workspace from the FULL snapshot (independent of any
  // filter); drop the focus if it vanished or stopped working. Overview-only.
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
    <div className="mx-auto flex w-full max-w-[100rem] flex-1 flex-col gap-5 p-4 pb-[env(safe-area-inset-bottom)]">
      <div className="flex items-center justify-end">
        <LandingViewToggle value={view} onChange={setLandingView} />
      </div>

      {error && !snapshot && (
        <div
          role="alert"
          className="rounded-md border border-[var(--status-error)] bg-[var(--status-error)]/10 p-4 text-sm"
        >
          Could not reach the daemon: {error.message}
        </div>
      )}

      {view === "hero" ? (
        <div className="mx-auto flex w-full max-w-3xl flex-col items-center gap-4 pt-2 sm:pt-6">
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
          <RecentStrip snapshot={snapshot} />
        </div>
      ) : (
        <>
          {live && (
            <FocusedPane workspaceId={live.state.id} title={live.state.title} onClose={clearLive} />
          )}
          {!snapshot ? !error && <SkeletonGrid /> : <WorkspaceGrid snapshot={snapshot} />}
        </>
      )}
    </div>
  );
}

/**
 * The persisted `Hero | Overview` segmented control (design §4.1). A quiet
 * two-button group on a `bg-muted` well — never the terracotta accent (that is
 * reserved for the send CTA). Test seams: `landing-view-toggle`,
 * `landing-view-hero`, `landing-view-overview`.
 */
function LandingViewToggle({
  value,
  onChange,
}: {
  value: LandingView;
  onChange: (v: LandingView) => void;
}) {
  return (
    <div
      role="group"
      aria-label="Landing view"
      data-testid="landing-view-toggle"
      className="inline-flex items-center gap-0.5 rounded-lg bg-muted/60 p-0.5 text-xs"
    >
      <ToggleButton
        active={value === "hero"}
        onClick={() => onChange("hero")}
        testid="landing-view-hero"
      >
        <MessageSquarePlus className="size-3.5" aria-hidden />
        Hero
      </ToggleButton>
      <ToggleButton
        active={value === "overview"}
        onClick={() => onChange("overview")}
        testid="landing-view-overview"
      >
        <LayoutGrid className="size-3.5" aria-hidden />
        Overview
      </ToggleButton>
    </div>
  );
}

function ToggleButton({
  active,
  onClick,
  testid,
  children,
}: {
  active: boolean;
  onClick: () => void;
  testid: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      data-testid={testid}
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 font-medium transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
        active
          ? "bg-card text-foreground shadow-sm"
          : "text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

/**
 * The Hero "Recent" strip (design §4.1 wireframe) — the most-recently-active
 * workspaces as quick links, so the clean composer surface still offers a
 * one-click return to live work without opening the rail. Newest-first by
 * `updated_at`; hidden entirely when there is nothing recent.
 */
function RecentStrip({ snapshot }: { snapshot: DashboardSnapshotView | null }) {
  const recents = useMemo(() => {
    const all = (snapshot?.projects ?? []).flatMap((p) => p.workspaces.map((w) => w.state));
    return [...all]
      .sort((a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? ""))
      .slice(0, 6);
  }, [snapshot]);

  if (recents.length === 0) return null;
  return (
    <div
      data-testid="composer-recent"
      className="flex flex-wrap items-center justify-center gap-x-1.5 gap-y-1 text-xs text-muted-foreground"
    >
      <span className="text-muted-foreground/70">Recent</span>
      {recents.map((w) => (
        <Link
          key={w.id}
          href={`/w/${w.id}`}
          data-testid="composer-recent-item"
          title={w.title}
          className="max-w-40 truncate rounded-md px-1.5 py-0.5 text-foreground/80 hover:bg-muted/60 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
        >
          {w.title}
        </Link>
      ))}
    </div>
  );
}

function SkeletonGrid() {
  return (
    <div className="flex flex-col gap-3">
      <Skeleton className="h-5 w-40" />
      <div className="grid grid-cols-[repeat(auto-fill,minmax(22rem,1fr))] gap-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-24 w-full rounded-xl" />
        ))}
      </div>
    </div>
  );
}
