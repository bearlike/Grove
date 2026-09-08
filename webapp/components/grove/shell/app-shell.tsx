"use client";

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
} from "react";

import { Sheet, SheetClose, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { XIcon } from "lucide-react";
import { cn } from "@/lib/utils";
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

/** The app's one shell, fleet stream, search controller, and app-level overlays. */
export function AppShell({ children }: { children: React.ReactNode }) {
  const collapsed = useSidebarUi((state) => state.collapsed);
  const mobileOpen = useSidebarUi((state) => state.mobileOpen);
  const setMobileOpen = useSidebarUi((state) => state.setMobileOpen);
  const stream = useFleetStream();
  const project = useProjectContext(stream.snapshot?.projects ?? []);
  const search = useFleetSearch(stream.snapshot, project);
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
    <div className="relative flex h-dvh w-full overflow-hidden">
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
  );
}
