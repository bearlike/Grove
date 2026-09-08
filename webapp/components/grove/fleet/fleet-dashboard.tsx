"use client";

import { useMemo, useState } from "react";
import { PlusIcon } from "lucide-react";

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
import { FleetFilterMenu } from "./fleet-filter";
import { useFleetSnapshot } from "./use-fleet";
import { WorkspaceCard } from "./workspace-card";
import { ProjectHeading } from "./project-heading";

/** The rail's warm snapshot and filter vocabulary, composed as a card wall. */
export function FleetDashboard(): React.ReactNode {
  const query = useFleetSnapshot();
  const [filter, setFilter] = useState<FleetFilter>(NO_FILTER);
  const openCreate = useCreateWorkspaceUi((state) => state.openFor);

  const rows = useMemo(() => sortedFleetRows(query.data), [query.data]);
  const facets = useMemo(() => fleetFacets(rows), [rows]);
  const visible = useMemo(() => filterRows(rows, filter), [rows, filter]);
  const groups = useMemo(() => fleetGroups(visible, filter.groupBy), [visible, filter.groupBy]);

  const firstRepoRoot = query.data?.projects[0]?.repo_root ?? "";
  const filtering = filter.query.trim() !== "" || activeFilterCount(filter) > 0;

  return (
    <div className="flex flex-col gap-5" data-testid="fleet-dashboard">
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
            {workspaceCountLabel(visible.length, rows.length)}
          </span>
          <Button variant="outline" size="sm" className="ml-auto" onClick={() => openCreate(firstRepoRoot)} data-testid="fleet-create">
            <PlusIcon aria-hidden /> New workspace
          </Button>
        </div>
      </div>

      {query.error ? (
        <ErrorState
          className="max-w-none"
          title="Cannot reach the daemon"
          detail={query.error.message}
          retrying={query.isFetching}
          onRetry={() => void query.refetch()}
        />
      ) : null}

      {query.isPending ? <DashboardSkeleton /> : null}

      {!query.isPending && visible.length === 0 ? (
        <FleetEmptyState
          filtered={filtering}
          onCreate={() => openCreate(firstRepoRoot)}
          onClearFilter={() => setFilter({ ...NO_FILTER, groupBy: filter.groupBy })}
        />
      ) : null}

      {groups.map((group) => (
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
              />
            ))}
          </div>
        </section>
      ))}
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
