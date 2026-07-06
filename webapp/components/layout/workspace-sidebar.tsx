"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Activity, ArrowUpCircle, Github, Search, Server, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { SessionRail } from "@/components/layout/session-rail";
import { SidebarFilter } from "@/components/layout/sidebar-filter";
import { useActivityStream, useDaemonWhoami } from "@/lib/grove/hooks";
import { computeFacets, type DashboardFacets } from "@/lib/grove/dashboard-filter";
import { useUiStore } from "@/lib/grove/ui-store";
import { cn } from "@/lib/utils";

const REPO_URL = "https://github.com/bearlike/Grove";

/**
 * The persistent left rail (ADE reframe, #140). Its scrollable BODY is now the
 * per-project session tree (`SessionRail`) — Grove's fleet moved out of the
 * center card grid into a "thread list" that is the fastest surface in the app.
 * The old scope/state/attention filter rail is gone (design §4.9 drops the
 * standalone filter rail); what survives folds in here: the search box (top,
 * filters the tree AND the Overview grid via the one `query` store field) and
 * the daemon/identity footer (bottom). The frozen shell contract is honored —
 * search stays at the top, `RailFooter` at the bottom, and only the body between
 * them changed.
 *
 * Collapse is fully outside this component now (#152, modern-chat behavior): the
 * shell layout animates the rail wrapper to `w-0` so the rail hides entirely —
 * there is no collapsed icon-strip variant. This component always renders its
 * full form; the header toggle + `[` shortcut own show/hide.
 *
 * Data comes from the authoritative `useActivityStream().snapshot`
 * (`computeFacets` → the project list + live counts) plus the per-project
 * `GET /sessions?repo=` reads the `SessionRail` owns. Positioning is the
 * consumer's job (the shell passes sticky rail classes; a second instance rides
 * a mobile `Sheet`). Test seams: `workspace-sidebar`, `sidebar-search`,
 * `sidebar-footer`, and the `session-rail*` seams the body owns.
 */
export function WorkspaceSidebar({
  className,
  onClose,
  onNavigate,
}: {
  className?: string;
  /** Mobile (Sheet) only — renders the close control. Desktop rail omits it. */
  onClose?: () => void;
  /** Called after a scope/filter/row click so the mobile drawer can dismiss itself. */
  onNavigate?: () => void;
}) {
  const { snapshot } = useActivityStream();
  const facets = useMemo<DashboardFacets | null>(
    () => (snapshot ? computeFacets(snapshot) : null),
    [snapshot],
  );
  const query = useUiStore((s) => s.query);
  const hiddenStates = useUiStore((s) => s.hiddenStates);
  const hiddenProjects = useUiStore((s) => s.hiddenProjects);
  const attentionOnly = useUiStore((s) => s.attentionOnly);
  const showUnmapped = useUiStore((s) => s.showUnmapped);
  const setShowUnmapped = useUiStore((s) => s.setShowUnmapped);
  const clearFilters = useUiStore((s) => s.clearFilters);

  return (
    <aside
      data-testid="workspace-sidebar"
      className={cn("flex min-h-0 flex-col bg-sidebar text-sidebar-foreground", className)}
    >
      <div className="flex items-center gap-2 px-3 pt-3">
        <SidebarSearch />
        <SidebarFilter projects={facets?.projects ?? []} />
        {onClose && (
          <Button
            variant="ghost"
            size="icon-sm"
            className="shrink-0 lg:hidden"
            aria-label="Close sidebar"
            data-testid="sidebar-close"
            onClick={onClose}
          >
            <X />
          </Button>
        )}
      </div>

      <Separator className="mt-3 bg-sidebar-border" />

      {/* `flex-1` claims the height between the search and footer; native scroll
          owns the overflow (the `ScrollArea` wrapper is retired app-wide — the
          global `scrollbar-color` rule styles this, design §4.7). */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {/* Suspense boundary for the rail's useSearchParams (`?s=`) so the
            static prerender of `/` + `/activity` succeeds — see
            app/login/page.tsx for the same pattern. */}
        <Suspense fallback={null}>
          <SessionRail
            snapshot={snapshot}
            facets={facets}
            query={query}
            hiddenStates={hiddenStates}
            attentionOnly={attentionOnly}
            hiddenProjects={hiddenProjects}
            showUnmapped={showUnmapped}
            onShowUnmapped={() => setShowUnmapped(true)}
            onClearFilters={clearFilters}
            onNavigate={onNavigate}
          />
        </Suspense>
      </div>

      <RailFooter facets={facets} />
    </aside>
  );
}

/** Free-text search → the store's `query` (filters the session tree AND grid). */
function SidebarSearch(): React.ReactElement {
  const query = useUiStore((s) => s.query);
  const setQuery = useUiStore((s) => s.setQuery);
  return (
    <div className="relative min-w-0 flex-1">
      <Search
        aria-hidden
        className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground"
      />
      <Input
        type="search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search sessions"
        aria-label="Search sessions"
        data-testid="sidebar-search"
        className="h-8 border-sidebar-border bg-card pl-8 text-[13px] placeholder:text-muted-foreground/70"
      />
    </div>
  );
}

// ─── Footer: daemon system status + user identity ────────────────────────────
// The deleted status bar's system context (daemon health + uptime, version +
// update nudge, workspace count, GitHub link — ADE reframe, #138) lives here,
// above the user-identity row. Testids preserved verbatim (`daemon-status`,
// `daemon-uptime`, `daemon-version`, `update-available`, `sidebar-footer`).

/**
 * Compact uptime renderer: at most two units, largest non-zero first.
 *
 * Examples: 5 -> "5s", 65 -> "1m 5s", 3700 -> "1h 1m", 90061 -> "1d 1h",
 * 0 / negative -> "0s". Two-unit cap keeps the footer strip tight.
 */
export function formatUptime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "0s";
  const s = Math.floor(seconds);
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (d > 0) return h > 0 ? `${d}d ${h}h` : `${d}d`;
  if (h > 0) return m > 0 ? `${h}h ${m}m` : `${h}h`;
  if (m > 0) return sec > 0 ? `${m}m ${sec}s` : `${m}m`;
  return `${sec}s`;
}

/** Daemon reachability + live uptime, shared by both footers. */
function useDaemonUptime() {
  const { data: whoami, isError } = useDaemonWhoami();
  const isReachable = !!whoami && !isError;
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);
  const liveUptime = whoami
    ? Math.max(0, Math.floor((now - new Date(whoami.started_at).getTime()) / 1000))
    : 0;
  return { whoami, isReachable, liveUptime };
}

function RailFooter({ facets }: { facets: DashboardFacets | null }): React.ReactElement {
  const { whoami, isReachable, liveUptime } = useDaemonUptime();

  return (
    <>
      <Separator className="bg-sidebar-border" />
      <div className="flex flex-col gap-1.5 px-3 py-2.5 text-[11px] text-muted-foreground">
        {/* Daemon health + uptime. */}
        <span className="inline-flex items-center gap-1.5" data-testid="daemon-status">
          <Server className="size-3 shrink-0" aria-hidden />
          <span
            aria-hidden
            className="inline-block size-1.5 rounded-full"
            style={{
              backgroundColor: isReachable ? "var(--status-active)" : "var(--status-error)",
            }}
          />
          <span className="font-medium text-foreground">
            {isReachable ? "online" : "unreachable"}
          </span>
          {whoami && (
            <>
              <span className="text-muted-foreground/60">·</span>
              <span className="tabular-nums" data-testid="daemon-uptime">
                up {formatUptime(liveUptime)}
              </span>
            </>
          )}
        </span>

        {/* Version + update nudge + workspace count. */}
        <span className="inline-flex items-center gap-2">
          {whoami && (
            <span className="inline-flex items-center gap-1.5">
              <span className="font-mono text-[10px]" data-testid="daemon-version">
                v{whoami.version}
              </span>
              {whoami.update_available && (
                <Link
                  href={`${REPO_URL}/releases/latest`}
                  target="_blank"
                  rel="noopener noreferrer"
                  data-testid="update-available"
                  aria-label={`Grove ${whoami.latest_version ? `v${whoami.latest_version} ` : ""}is available — view the release`}
                  className="inline-flex items-center gap-1 rounded-sm font-medium hover:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
                  style={{ color: "var(--status-orphaned)" }}
                >
                  <ArrowUpCircle className="size-3" aria-hidden />
                  <span>{whoami.latest_version ? `v${whoami.latest_version}` : "update"}</span>
                </Link>
              )}
            </span>
          )}
          {facets && (
            <span className="inline-flex items-center gap-1 tabular-nums">
              <Activity className="size-3 shrink-0" aria-hidden />
              {facets.total} ws
            </span>
          )}
        </span>
      </div>

      {/* User identity row + external GitHub link. */}
      {whoami && (
        <>
          <Separator className="bg-sidebar-border" />
          <div
            data-testid="sidebar-footer"
            className="flex items-center gap-2 px-3 py-2.5 text-xs text-muted-foreground"
          >
            <span
              aria-hidden
              className="grid size-6 shrink-0 place-items-center rounded-md bg-accent text-[11px] font-semibold uppercase leading-none text-foreground"
            >
              {whoami.user.slice(0, 1)}
            </span>
            <span className="min-w-0 flex-1 truncate font-mono text-[11px]">
              {whoami.user}@{whoami.host}
            </span>
            <Link
              href={REPO_URL}
              target="_blank"
              rel="noopener noreferrer"
              aria-label="Grove on GitHub"
              className="inline-flex shrink-0 items-center rounded-sm p-0.5 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
            >
              <Github className="size-3.5" aria-hidden />
            </Link>
          </div>
        </>
      )}
    </>
  );
}
