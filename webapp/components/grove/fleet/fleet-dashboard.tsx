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
  NO_FILTER,
  sortedFleetRows,
  workspaceCountLabel,
  type FleetFilter,
} from "./filter";
import { FleetFilterMenu } from "./fleet-filter";
import { useFleetSnapshot } from "./use-fleet";
import { WorkspaceCard } from "./workspace-card";

/**
 * The whole fleet on one wall: one flat list, newest activity first.
 *
 * It used to be a section per repo, and that was wrong for the same reason the
 * rail's grouping was: most projects hold no workspace at any given moment, so
 * the wall spent most of a viewport on headings reading "0 — No workspaces
 * yet". Project is a property of a workspace, not a container for one, so it
 * appears ON the card and as a dimension in the filter menu. A repo with
 * nothing in it now takes up no space at all.
 *
 * The filter is the SAME component the rail uses — one vocabulary, one set of
 * hide-semantics, so narrowing here and narrowing there mean the same thing.
 *
 * It reads the cache the shell's stream keeps warm — no second subscription,
 * no second fetch.
 */
export function FleetDashboard(): React.ReactNode {
  const query = useFleetSnapshot();
  const [filter, setFilter] = useState<FleetFilter>(NO_FILTER);
  const openCreate = useCreateWorkspaceUi((state) => state.openFor);

  const rows = useMemo(() => sortedFleetRows(query.data), [query.data]);
  const facets = useMemo(() => fleetFacets(rows), [rows]);
  const visible = useMemo(() => filterRows(rows, filter), [rows, filter]);

  const firstRepoRoot = query.data?.projects[0]?.repo_root ?? "";
  const filtering = filter.query.trim() !== "" || activeFilterCount(filter) > 0;

  return (
    <div className="flex flex-col gap-5" data-testid="fleet-dashboard">
      {/* The route title names the destination. This lead names the job the page
          performs, then keeps the scan tools together as one instrument band. */}
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="min-w-0">
            <p className="text-lg font-medium text-content-primary">Your workspaces</p>
            <p className="text-sm text-content-secondary">
              Recent activity across every project, ready to resume.
            </p>
          </div>
          {/* ONE create button. Each repo used to carry its own, which is five
              buttons to say one thing — the dialog already asks which repo. */}
          <Button onClick={() => openCreate(firstRepoRoot)} data-testid="fleet-create">
            <PlusIcon aria-hidden />
            New workspace
          </Button>
        </div>

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
              className="h-9"
              data-testid="fleet-search"
            />
          </div>
          <FleetFilterMenu filter={filter} onFilterChange={setFilter} facets={facets} />
          <span className="text-xs text-content-tertiary tabular-nums">
            {workspaceCountLabel(visible.length, rows.length)}
          </span>
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
          onClearFilter={() => setFilter(NO_FILTER)}
        />
      ) : null}

      {visible.length > 0 ? (
        <section aria-label="Recent workspaces" className="flex flex-col gap-3">
          <div className="flex items-center gap-2">
            <p className="text-sm font-medium text-content-primary">Recent activity</p>
            <span className="text-xs text-content-tertiary">Newest first</span>
          </div>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(22rem,1fr))] gap-3">
            {visible.map((row) => (
              <WorkspaceCard
                key={row.workspace.state.id}
                workspace={row.workspace}
                repoName={row.repoName}
              />
            ))}
          </div>
        </section>
      ) : null}
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
    <div className="grid grid-cols-[repeat(auto-fill,minmax(22rem,1fr))] gap-3">
      {[0, 1, 2, 3].map((card) => (
        <Skeleton key={card} className="h-40 w-full" />
      ))}
    </div>
  );
}
