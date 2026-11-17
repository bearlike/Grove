"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { KanbanSquareIcon, LayoutGridIcon, PlusIcon } from "lucide-react";

import { useActivityStream, useMe } from "@/lib/grove/hooks";
import {
  EmptyState,
  EmptyStateGreeting,
  EmptyStateSuggestion,
  EmptyStateSuggestions,
} from "@/components/elements/empty-state";
import { ErrorState } from "@/components/elements/error-state";
import { ThreadListSearch } from "@/components/assistant-ui/thread-list";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useCreateWorkspaceUi } from "./create-store";
import {
  activeFilterCount,
  filterRows,
  fleetFacets,
  fleetGroups,
  NO_FILTER,
  sortedFleetRows,
  workspaceCountLabel,
  type FleetFilter,
} from "./filter";
import { FleetBoard } from "./fleet-board";
import { FleetBulkBar } from "./fleet-bulk-bar";
import { FleetFilterMenu } from "./fleet-filter";
import { FleetProgressStrip } from "./fleet-progress";
import { fleetProgress, NO_SELECTION, pruneSelection, toggleSelected, type Selection } from "./kanban";
import { useFleetSnapshot } from "./use-fleet";
import { WorkspaceCard } from "./workspace-card";
import { ProjectHeading } from "./project-heading";

/** The two arrangements of the same filtered rows. */
type FleetView = "board" | "cards";
const VIEW_KEY = "grove.fleet.view";

/**
 * Which arrangement the reader last chose. Restored after mount, like every
 * other `localStorage` read here, because the server cannot know it.
 */
function useFleetView(): [FleetView, (view: FleetView) => void] {
  const [view, setView] = useState<FleetView>("board");
  useEffect(() => {
    const stored = window.localStorage.getItem(VIEW_KEY);
    if (stored === "board" || stored === "cards") setView(stored);
  }, []);
  return [
    view,
    (next) => {
      window.localStorage.setItem(VIEW_KEY, next);
      setView(next);
    },
  ];
}

/** The rail's warm snapshot and filter vocabulary, as a phase board or a card wall. */
export function FleetDashboard(): React.ReactNode {
  const query = useFleetSnapshot();
  const { refetch } = useActivityStream();
  const [filter, setFilter] = useState<FleetFilter>(NO_FILTER);
  const [view, setView] = useFleetView();
  const [selected, setSelected] = useState<Selection>(NO_SELECTION);
  const openCreate = useCreateWorkspaceUi((state) => state.setOpen);

  const rows = useMemo(() => sortedFleetRows(query.data), [query.data]);
  const facets = useMemo(() => fleetFacets(rows), [rows]);
  const me = useMe().data?.subject;
  const visible = useMemo(() => filterRows(rows, filter, me), [rows, filter, me]);
  const workers = visible;
  const groups = useMemo(() => fleetGroups(workers, filter.groupBy), [workers, filter.groupBy]);
  const progress = useMemo(() => fleetProgress(workers), [workers]);

  // A selection is only ever over what is on screen: a narrower filter drops
  // the hidden ids, so a bulk verb cannot reach a row the reader cannot see.
  useEffect(() => {
    setSelected((held) => pruneSelection(held, workers));
  }, [workers]);
  const toggle = useCallback((id: string): void => setSelected((held) => toggleSelected(held, id)), []);

  const filtering = filter.query.trim() !== "" || activeFilterCount(filter) > 0;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4" data-testid="fleet-dashboard" data-view={view}>
      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          {/* The rail's search control, not a second one. It depends on no
              thread-list primitive — it forwards to `Input` — and brings the
              leading glyph and `type="search"` a bare `Input` here did not, so
              the fleet's two search boxes are one control. `h-9` matches this
              toolbar's 36px buttons; the rail's pair are 32 together. */}
          <div className="w-full max-w-xs">
            <ThreadListSearch
              value={filter.query}
              onValueChange={(query) => setFilter({ ...filter, query })}
              placeholder="Search workspaces, branches, tickets"
              aria-label="Search the fleet"
              className="h-8"
              data-testid="fleet-search"
            />
          </div>
          <FleetFilterMenu filter={filter} onFilterChange={setFilter} facets={facets} />
          <span className="text-xs text-content-tertiary tabular-nums">
            {workspaceCountLabel(workers.length, rows.length)}
          </span>
          <Tabs value={view} onValueChange={(next) => setView(next as FleetView)} className="ml-auto">
            <TabsList variant="line" className="h-8" aria-label="Arrange the fleet">
              <TabsTrigger value="board" className="gap-1.5 text-xs" data-testid="fleet-view-board">
                <KanbanSquareIcon className="size-3.5" aria-hidden /> Board
              </TabsTrigger>
              <TabsTrigger value="cards" className="gap-1.5 text-xs" data-testid="fleet-view-cards">
                <LayoutGridIcon className="size-3.5" aria-hidden /> Cards
              </TabsTrigger>
            </TabsList>
          </Tabs>
          <Button variant="outline" size="sm" onClick={() => openCreate(true)} data-testid="fleet-create">
            <PlusIcon aria-hidden /> New workspace
          </Button>
        </div>
        <FleetBulkBar visible={workers} selected={selected} onSelectionChange={setSelected} />
      </div>

      {query.error ? (
        <ErrorState
          className="max-w-none"
          title="Cannot reach the daemon"
          detail={query.error.message}
          retrying={query.isFetching}
          onRetry={refetch}
        />
      ) : null}

      {query.isPending ? <DashboardSkeleton /> : null}

      {!query.isPending && workers.length > 0 ? <FleetProgressStrip progress={progress} /> : null}

      {!query.isPending && workers.length === 0 ? (
        <FleetEmptyState
          filtered={filtering}
          onCreate={() => openCreate(true)}
          onClearFilter={() => setFilter({ ...NO_FILTER, groupBy: filter.groupBy, foldEmpty: filter.foldEmpty })}
        />
      ) : null}

      {view === "board" && workers.length > 0 ? (
        <FleetBoard rows={workers} selected={selected} onToggleSelected={toggle} grouped={false} hideEmpty={filter.foldEmpty} />
      ) : null}

      {view === "cards"
        ? groups.map((group) => (
            <section key={group.key} aria-label={group.repoName ? `${group.repoName} workspaces` : "Recent workspaces"} className="flex min-w-0 flex-col gap-3" data-testid="fleet-dashboard-group">
              {group.repoName ? (
                <ProjectHeading name={group.repoName} count={group.rows.length} />
              ) : (
                <h2 className="text-xs font-medium text-content-tertiary">Latest activity · inactive last</h2>
              )}
              <div className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,22rem),1fr))] gap-3">
                {group.rows.map((row) => (
                  <WorkspaceCard
                    key={row.workspace.state.id}
                    workspace={row.workspace}
                    repoName={row.repoName}
                    grouped={filter.groupBy === "project"}
                    selected={selected.has(row.workspace.state.id)}
                    onSelectedChange={toggle}
                  />
                ))}
              </div>
            </section>
          ))
        : null}
    </div>
  );
}

/**
 * Two very different nothings: a fleet you have not started yet, and a filter
 * that matched none of one you have. Offering "create a workspace" to someone
 * whose search simply missed is the wrong door.
 */
function FleetEmptyState({
  filtered,
  onCreate,
  onClearFilter,
}: {
  filtered: boolean;
  onCreate: () => void;
  onClearFilter: () => void;
}): React.ReactNode {
  return (
    <div className="flex justify-center py-12">
      <EmptyState data-testid="fleet-empty">
        <EmptyStateGreeting>
          {filtered ? "Nothing matches that." : "No workspaces yet."}
        </EmptyStateGreeting>
        <EmptyStateSuggestions>
          {filtered ? (
            <EmptyStateSuggestion onClick={onClearFilter}>Clear the filter</EmptyStateSuggestion>
          ) : (
            <EmptyStateSuggestion onClick={onCreate}>Create a workspace</EmptyStateSuggestion>
          )}
        </EmptyStateSuggestions>
      </EmptyState>
    </div>
  );
}

function DashboardSkeleton(): React.ReactNode {
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,22rem),1fr))] gap-3">
      {[0, 1, 2, 3].map((card) => (
        <Skeleton key={card} className="h-48 w-full" />
      ))}
    </div>
  );
}
