"use client";

import { useMemo, useState } from "react";

import { EmptyState, EmptyStateGreeting } from "@/components/elements/empty-state";
import { ErrorState } from "@/components/elements/error-state";
import {
  filterSessions,
  maxTurnCount,
  NO_SESSION_FILTER,
  sessionCountLabel,
  sessionFacets,
  type SessionFilter,
} from "@/components/grove/sessions/filter";
import { SessionFilterMenu } from "@/components/grove/sessions/filter-menu";
import { SessionTable } from "@/components/grove/sessions/session-table";
import { ShellHeader } from "@/components/grove/shell/shell-header";
import { FROZEN_TABLE_HEAD } from "@/components/grove/table-columns";
import { ThreadListSearch } from "@/components/assistant-ui/thread-list";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useSessionCatalog } from "@/lib/grove/hooks";

/**
 * The host-wide session catalog: every agent session on this machine, whether
 * Grove launched it or not.
 *
 * A ROUTE, not a feature — the table, the filter menu and the narrowing rules
 * live in `components/grove/sessions/`, and what is left here is the page's own
 * shape: one toolbar row, one scroll owner, one count.
 *
 * THE SCROLL OWNER IS THE TABLE'S BOX, not the page. A page that scrolls takes
 * its toolbar and its header row with it, and this is the surface a person
 * scans hundreds of rows on — so the toolbar and the count stay put while the
 * table scrolls in the height left over, and `FROZEN_TABLE_HEAD` keeps the
 * header visible while it does. That constant is shared rather than local
 * precisely so this page invents nothing: every scrollable table in the app is
 * meant to freeze its header the same way.
 */
/**
 * How many rows this page asks for, stated rather than inherited.
 *
 * `GET /sessions` caps at a `limit` it chooses (50) and returns no grand total,
 * so asking for nothing meant the page could not tell "this host has 50
 * sessions" from "this host has 400 and you are seeing 50" — and it printed the
 * former. Naming the number here is what lets the count line below say which it
 * is: ask for a bound you know, and you can state it when you hit it.
 *
 * 200 is the route's own ceiling. The scan cost is unchanged — the daemon
 * builds every row and slices — so this buys the honest answer for four times
 * the JSON and nothing else.
 */
const CATALOG_LIMIT = 200;

export default function SessionsPage(): React.ReactNode {
  const [filter, setFilter] = useState<SessionFilter>(NO_SESSION_FILTER);
  const catalog = useSessionCatalog(CATALOG_LIMIT);
  const sessions = useMemo(() => catalog.data ?? [], [catalog.data]);

  // Facets and the ceiling are derived from the UNFILTERED rows: an option that
  // counted only what survives would read `0` the moment it was switched off.
  const facets = useMemo(() => sessionFacets(sessions), [sessions]);
  const ceiling = useMemo(() => maxTurnCount(sessions), [sessions]);
  const shown = useMemo(() => filterSessions(sessions, filter), [sessions, filter]);

  const settled = !catalog.isLoading && !catalog.error;

  return (
    <>
      <ShellHeader />
      <main className="flex min-h-0 min-w-0 flex-1 flex-col gap-3 p-4" data-testid="sessions-page">
        <div className="flex shrink-0 items-center gap-2">
          {/* The SAME search control the rail uses. It reads as a thread-list
              component and is not one — it depends on no primitive, forwards
              everything to `Input`, and carries the leading glyph and
              `type="search"` (so the browser's own clear affordance appears)
              that a bare `Input` here did not. Two searches in one app must not
              look like two kinds of control.

              `h-9` overrides its native `h-8` because this toolbar's controls
              are 36px, measured: the filter button beside it is 36 and the
              rail's pair are both 32. Matching the neighbour is the rule; the
              wrapper's own 4px padding just gives the row a little more air. */}
          <div className="min-w-0 flex-1">
            <ThreadListSearch
              value={filter.query}
              onValueChange={(query) => setFilter({ ...filter, query })}
              placeholder="Search sessions"
              aria-label="Search sessions"
              className="h-9"
            />
          </div>
          <SessionFilterMenu
            filter={filter}
            onFilterChange={setFilter}
            facets={facets}
            maxTurns={ceiling}
          />
        </div>

        {catalog.isLoading ? <SessionSkeleton /> : null}
        {catalog.error ? (
          <ErrorState
            title="Sessions unavailable"
            detail={catalog.error.message}
            retrying={catalog.isFetching}
            onRetry={() => void catalog.refetch()}
          />
        ) : null}

        {/* TWO empty states, not one with two strings. "You have no sessions"
            is a fact about the host and there is nothing to do about it; "your
            filters match nothing" is a state the reader created and can undo,
            so it carries the undo. A single message would leave someone who
            narrowed too far staring at what looks like an empty machine. */}
        {settled && shown.length === 0 && sessions.length === 0 ? (
          <EmptyState className="mx-auto my-auto" data-testid="sessions-empty">
            <EmptyStateGreeting>No agent sessions found</EmptyStateGreeting>
          </EmptyState>
        ) : null}

        {settled && shown.length === 0 && sessions.length > 0 ? (
          <EmptyState className="mx-auto my-auto" data-testid="sessions-empty-filtered">
            <EmptyStateGreeting>No sessions match these filters</EmptyStateGreeting>
            <Button
              variant="outline"
              size="sm"
              className="mt-3"
              onClick={() => setFilter(NO_SESSION_FILTER)}
              data-testid="sessions-clear-filters"
            >
              Clear filters and search
            </Button>
          </EmptyState>
        ) : null}

        {settled && shown.length > 0 ? (
          <>
            <div className={`min-h-0 flex-1 ${FROZEN_TABLE_HEAD}`} data-testid="sessions-scroller">
              <SessionTable sessions={shown} />
            </div>
            {/* Stated, never silent — and it has to state BOTH bounds. A
                narrowed list that does not say it is narrowed reads as the
                whole catalog; a capped list that does not say it is capped
                reads as the whole host. This line was doing the first and not
                the second. */}
            <p
              className="text-content-tertiary shrink-0 text-xs tabular-nums"
              role="status"
              data-testid="sessions-count"
            >
              {sessionCountLabel(shown.length, sessions.length, sessions.length >= CATALOG_LIMIT)}
            </p>
          </>
        ) : null}
      </main>
    </>
  );
}

/**
 * The table that is about to be here, in the box it will occupy.
 *
 * `min-h-0 flex-1` is the load-bearing part, not the bars: a skeleton at its
 * natural height left the rest of the column empty, so when the catalog landed
 * the table claimed the whole remaining height and the count line appeared
 * underneath it — the toolbar held still and everything below it moved. Taking
 * the same box means the only thing that changes is what the rows say.
 *
 * The first bar is the header row, at the header's height. A skeleton that
 * omits it under-states the table by exactly one row and shifts every row up
 * when the real header arrives.
 */
function SessionSkeleton(): React.ReactNode {
  return (
    <div
      className="flex min-h-0 flex-1 flex-col gap-2 overflow-hidden"
      role="status"
      aria-label="Loading sessions"
      data-testid="sessions-skeleton"
    >
      <Skeleton className="h-8 w-full shrink-0" />
      {Array.from({ length: 12 }, (_, index) => (
        <Skeleton key={index} className="h-10 w-full shrink-0" />
      ))}
    </div>
  );
}
