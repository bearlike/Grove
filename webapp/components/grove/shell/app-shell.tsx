"use client";

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
} from "react";

import { usePathname } from "next/navigation";

import { Sheet, SheetClose, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { XIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  accountSummaries,
  EMPTY_COUNTS,
  EMPTY_PROGRESS,
  fleetAttention,
  fleetCounts,
  fleetProgress,
  projectContext,
  systemFacts,
  workspaceContext,
} from "@/lib/grove/adapters";
import { useUsageQuotas } from "@/lib/grove/hooks/usage";
import { useWhoami } from "@/lib/grove/hooks";
import { StatusFooter } from "@/components/grove/shell/status-footer";
import { AnnotationHost } from "@/components/grove/annotation";
import { FleetOverlays, useFleetStream } from "@/components/grove/fleet";
import {
  filterRows,
  fleetFacets,
  NO_FILTER,
  sortedFleetRows,
  type FleetFilter,
} from "@/components/grove/fleet/filter";
import { useProjectContext, scopeFleetRows } from "@/components/grove/fleet/project-context";
import type { FleetSearchController } from "@/components/grove/fleet/fleet-palette";
import { AppSidebar } from "@/components/grove/shell/app-sidebar";
import { RAIL_WIDTH } from "./rail-width";
import {
  useSidebarShortcuts,
  useSidebarUi,
} from "./sidebar-state";

/** Shell-owned state makes the two rail mounts and the single palette agree. */
function useFleetSearch(
  snapshot: ReturnType<typeof useFleetStream>["snapshot"],
  project: ReturnType<typeof useProjectContext>,
): FleetSearchController {
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState<FleetFilter>({ ...NO_FILTER, groupBy: "project" });
  const allRows = useMemo(() => sortedFleetRows(snapshot), [snapshot]);
  const scopedRows = useMemo(
    () => scopeFleetRows(allRows, project.context),
    [allRows, project.context],
  );
  const visibleRows = useMemo(
    () => filterRows(scopedRows, filter),
    [scopedRows, filter],
  );
  const facets = useMemo(() => fleetFacets(scopedRows), [scopedRows]);

  return useMemo(
    () => ({ open, filter, allRows, scopedRows, visibleRows, facets, setOpen, setFilter }),
    [allRows, facets, filter, open, scopedRows, visibleRows],
  );
}

/**
 * The status footer's four sections, from the queries the shell already holds.
 *
 * Reads the SAME fleet snapshot the rail renders rather than opening a second
 * subscription — the "one `EventSource` per APP" rule this shell already owns.
 * Quota and identity are ordinary cached queries; both are served from the
 * daemon's own TTL cache, so a permanently-mounted footer costs no upstream
 * request per render.
 *
 * Context is route-derived: on `/w/<id>` the open workspace answers, and
 * everywhere else the rail's selected project does. Mixing the two — one
 * project's name beside another workspace's branch — is the one thing this
 * band must never do, so exactly one of the two branches runs.
 */
function useFooterData(
  stream: ReturnType<typeof useFleetStream>,
  project: ReturnType<typeof useProjectContext>,
) {
  const pathname = usePathname();
  const quotas = useUsageQuotas();
  const whoami = useWhoami();

  const workspaceId = pathname.startsWith("/w/")
    ? (pathname.split("/")[2] ?? null)
    : null;
  const connected = stream.phase === "online" && !stream.error;

  const context = useMemo(() => {
    if (workspaceId !== null) {
      const open = workspaceContext(stream.snapshot, workspaceId);
      if (open !== null) return open;
    }
    return projectContext(stream.snapshot, project.selectedProjectCwd);
  }, [project.selectedProjectCwd, stream.snapshot, workspaceId]);

  // Counts are honest about the stream rather than the cache: a disconnected
  // stream holds whatever it last saw, and rendering that as a live count is
  // the silently-stale answer the footer's own contract refuses.
  const counts = useMemo(
    () => (connected ? fleetCounts(stream.snapshot) : EMPTY_COUNTS),
    [connected, stream.snapshot],
  );
  const accounts = useMemo(() => accountSummaries(quotas.data), [quotas.data]);
  const system = useMemo(() => systemFacts(whoami.data), [whoami.data]);
  // Gated on the stream for the same reason the counts are: a cached snapshot
  // read as a live aggregate is the silently-stale answer.
  const progress = useMemo(
    () => (connected ? fleetProgress(stream.snapshot) : EMPTY_PROGRESS),
    [connected, stream.snapshot],
  );
  const attention = useMemo(
    () => (connected ? fleetAttention(stream.snapshot) : 0),
    [connected, stream.snapshot],
  );

  return { context, counts, accounts, system, connected, progress, attention };
}

/** The app's one shell, fleet stream, search controller, and app-level overlays. */
export function AppShell({ children }: { children: React.ReactNode }) {
  const collapsed = useSidebarUi((state) => state.collapsed);
  const mobileOpen = useSidebarUi((state) => state.mobileOpen);
  const setMobileOpen = useSidebarUi((state) => state.setMobileOpen);
  const stream = useFleetStream();
  const project = useProjectContext(stream.snapshot?.projects ?? []);
  const search = useFleetSearch(stream.snapshot, project);
  const footer = useFooterData(stream, project);
  const searchOpener = useRef<HTMLElement | null>(null);
  const [searchAfterSheet, setSearchAfterSheet] = useState(false);
  useSidebarShortcuts();

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key.toLowerCase() !== "k" || !(event.metaKey || event.ctrlKey)) return;
      event.preventDefault();
      if (!search.open) {
        searchOpener.current = document.activeElement instanceof HTMLElement
          ? document.activeElement
          : null;
      }
      if (mobileOpen) {
        setSearchAfterSheet(true);
        setMobileOpen(false);
        return;
      }
      search.setOpen(!search.open);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [mobileOpen, search, setMobileOpen]);

  useEffect(() => {
    if (mobileOpen || !searchAfterSheet) return;
    setSearchAfterSheet(false);
    search.setOpen(true);
  }, [mobileOpen, search, searchAfterSheet]);

  const openSearch = (event: ReactMouseEvent<HTMLButtonElement>): void => {
    searchOpener.current = event.currentTarget;
    if (mobileOpen) {
      setSearchAfterSheet(true);
      setMobileOpen(false);
      return;
    }
    search.setOpen(true);
  };

  const closeSearch = (): void => {
    search.setOpen(false);
  };

  const returnFocus = (): void => {
    const opener = searchOpener.current;
    if (opener?.isConnected) {
      opener.focus();
      return;
    }
    document.querySelector<HTMLButtonElement>("[data-testid=\"shell-sidebar-sheet\"]")?.focus();
  };

  return (
    // One column: the rail+page ROW, then the status footer beneath both.
    // The row keeps `relative` and `h-dvh`'s bounded height moves to the
    // column, so the rail's absolutely-positioned descendants still resolve
    // against the row that CONTAINS the rail — the trap `webapp/CLAUDE.md`
    // records as a page painting over the brand.
    <div className="flex h-dvh w-full flex-col overflow-hidden">
      <div className="relative flex min-h-0 w-full flex-1 overflow-hidden">
      <aside
        data-testid="app-sidebar"
        data-collapsed={collapsed}
        className={cn(
          "bg-surface-sunken hidden h-full shrink-0 flex-col overflow-hidden border-r border-border transition-[width] duration-200 md:flex",
          collapsed ? "w-12" : RAIL_WIDTH,
        )}
      >
        <AppSidebar
          collapsed={collapsed}
          stream={stream}
          project={project}
          search={search}
          onOpenSearch={openSearch}
        />
      </aside>

      <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
        <SheetContent
          side="left"
          showCloseButton={false}
          className="flex w-full flex-col p-0 sm:max-w-none"
        >
          <SheetTitle className="sr-only">Grove workspaces</SheetTitle>
          <div
            className="flex min-h-0 flex-1 flex-col"
            onClick={(event) => {
              if (
                event.target instanceof Element &&
                event.target.closest("a[href]")
              ) {
                setMobileOpen(false);
              }
            }}
          >
            <AppSidebar
              collapsed={false}
              stream={stream}
              project={project}
              search={search}
              onOpenSearch={openSearch}
              headerAction={
                <SheetClose asChild>
                  <TooltipIconButton
                    tooltip="Close"
                    aria-label="Close"
                    variant="outline"
                    className="bg-transparent dark:bg-transparent border-edge-control size-6 min-h-[24px] min-w-[24px] [@media(pointer:coarse)]:min-h-11 [@media(pointer:coarse)]:min-w-11"
                  >
                    <XIcon />
                  </TooltipIconButton>
                </SheetClose>
              }
            />
          </div>
        </SheetContent>
      </Sheet>

      <div className="bg-surface-sunken flex h-full min-w-0 flex-1 flex-col overflow-hidden px-2 pb-2 md:pl-0">
        <div className="shell-panel flex min-h-0 flex-1 flex-col">
          {/* The page, with an annotation pane beside it while one is open.
              Inside the panel so the pane shares the page's clipping corner. */}
          <AnnotationHost>{children}</AnnotationHost>
        </div>
      </div>

      <FleetOverlays
        snapshot={stream.snapshot}
        search={search}
        onSearchOpenChange={(open) => {
          if (open) search.setOpen(true);
          else closeSearch();
        }}
        onSearchCloseAutoFocus={returnFocus}
      />
      </div>

      <StatusFooter
        context={footer.context}
        counts={footer.counts}
        accounts={footer.accounts}
        system={footer.system}
        connected={footer.connected}
        progress={footer.progress}
        attention={footer.attention}
      />
    </div>
  );
}
