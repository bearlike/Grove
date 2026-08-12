"use client";

import { useState } from "react";

import {
  useRefreshUsage,
  useUsageActivity,
  useUsageBashCommands,
  useUsageBreakdown,
  useUsageFindings,
  useUsageQuotas,
  useUsageSeries,
  useUsageSessions,
  useUsageSummary,
} from "@/lib/grove/hooks";
import { DEFAULT_GROUPING, groupingFor, type SeriesGrouping } from "@/lib/grove/adapters";
import { UsageActivity } from "./activity";
import { UsageBashCommands } from "./bash-commands";
import { UsageSeries } from "./series";
import { UsageBreakdown } from "./breakdown";
import { UsageCost } from "./cost";
import { UsageCoverage } from "./coverage";
import { UsageFindings } from "./findings";
import { UsageQuota } from "./quota";
import { UsageSessions } from "./sessions";
import { UsageSummary } from "./summary";
import { UsageToolSplit } from "./tool-split";

const FILTERS = {};
const SESSION_LIMIT = 50;

/**
 * The heatmap's window: a rolling year, resolved ONCE per page load.
 *
 * The calendar is meant to show the last 365 days whatever the store's own
 * span happens to be, and `/usage/activity` fills the range with zero buckets
 * for days nothing ran — so asking for the window is what makes it a year-shaped
 * calendar instead of a ragged one.
 *
 * Computed at module scope, deliberately: a timestamp recomputed per render is
 * a new react-query key per render, which is an infinite refetch loop that
 * looks like a slow page rather than a bug.
 */
const ACTIVITY_FILTERS = {
  since: new Date(Date.now() - 365 * 24 * 60 * 60 * 1000).toISOString(),
};

/**
 * The mix chart's window: the last seven calendar days, BOTH bounds pinned.
 *
 * `until` is not optional here the way it is for the calendar. Left off, the
 * daemon ends the spine at the newest indexed event, so a fleet quiet since
 * Tuesday would render a four-day "last 7 days" — and the missing days are
 * exactly the finding. Module scope for the reason above: a timestamp
 * recomputed per render is a new query key per render.
 */
const SERIES_FILTERS = {
  since: new Date(Date.now() - 6 * 24 * 60 * 60 * 1000).toISOString(),
  until: new Date().toISOString(),
};

/**
 * The audit page: six independent queries, six independently readable cards.
 *
 * The row structure is the answer to "what am I looking at", top to bottom:
 * what this is based on → what was measured → what capacity is left → what it
 * cost → where it went → what the audit noticed. Every long collection is
 * BOUNDED inside its card, so the page is a fixed object that fits a screen or
 * two; the old one grew with the data and buried the headline numbers under
 * 2,475 findings.
 *
 * Nothing here blocks on anything else — each section renders its own skeleton,
 * because one slow query used to blank the whole route.
 *
 * Each also retries on its OWN, which is the same independence stated for the
 * failure path: the Refresh button in the coverage strip re-indexes the store,
 * which is a different and far heavier act than re-asking for one section that
 * happened to lose its request. A page whose only recovery is "re-index
 * everything" makes a dropped connection look like a corrupted store.
 */
export function UsageAudit(): React.ReactNode {
  const summary = useUsageSummary(FILTERS);
  const activity = useUsageActivity(ACTIVITY_FILTERS, "tokens");
  const quotas = useUsageQuotas();
  const sessions = useUsageSessions(FILTERS, { sort: "recent", limit: SESSION_LIMIT });
  const models = useUsageBreakdown(FILTERS, "model");
  // The same breakdown route, asked for a different dimension — the built-in
  // versus MCP classification is one prefix test on the row key, so it is a
  // render-site decision rather than a second endpoint.
  const tools = useUsageBreakdown(FILTERS, "tool");
  const bashCommands = useUsageBashCommands(FILTERS);
  const findings = useUsageFindings(FILTERS);
  const refresh = useRefreshUsage();
  // The grouping lives here rather than inside the card because the card is
  // presentational like every other panel on this page — the query is the
  // page's, so the vocabulary that selects it is too.
  const [grouping, setGrouping] = useState<SeriesGrouping["id"]>(DEFAULT_GROUPING);
  const selected = groupingFor(grouping);
  const series = useUsageSeries(SERIES_FILTERS, selected.id, selected.metric);

  return (
    <main
      className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4"
      data-testid="usage-page"
    >
      {/* Cost rides with coverage rather than beside the calendar: both are one
          short statement ABOUT the numbers, so they sit at equal height, and the
          calendar gets the full row its 53 weeks actually need. */}
      <div className="grid min-w-0 gap-4 xl:grid-cols-3">
        <UsageCoverage
          coverage={summary.data?.coverage}
          failed={summary.isError}
          onRetry={() => void summary.refetch()}
          retrying={summary.isFetching}
          onRefresh={() => refresh.mutate()}
          refreshing={refresh.isPending}
          note={refreshNote(refresh)}
          className="xl:col-span-2"
        />
        <UsageCost
          summary={summary.data}
          failed={summary.isError}
          onRetry={() => void summary.refetch()}
          retrying={summary.isFetching}
        />
      </div>

      <UsageSummary
        summary={summary.data}
        failed={summary.isError}
        onRetry={() => void summary.refetch()}
        retrying={summary.isFetching}
      />

      <UsageQuota
        quotas={quotas.data}
        failed={quotas.isError}
        onRetry={() => void quotas.refetch()}
        retrying={quotas.isFetching}
      />

      {/* An ELEVEN-unit row: the calendar takes seven, the week's mix four.
          It was 5:2 of seven, which gave the mix 28.6%; four elevenths is
          36.4%, a deliberate 27% widening because a stacked chart with a
          legend, a dropdown and two derived lines was doing more work than a
          two-sevenths column could show.

          The calendar narrowing is intended, and it degrades gracefully rather
          than breaking: its own `min-w-[56rem]` legibility floor still holds,
          so below that width it scrolls horizontally exactly as it already
          does on a small screen — a true 365 days either way, never shrunk to
          illegible dots. `min-w-0` on the row stays load-bearing: a grid child
          defaults to `min-width:auto`, which is how the calendar once pushed
          its own card wider than the page.

          Eleven rather than twelve because 4/12 is only a 17% widening, under
          the bar; Tailwind ships `grid-cols-11`, so this needs no arbitrary
          value.

          THE SPANS MUST SUM TO THE TRACK COUNT, and nothing enforces it. They
          read 7 + 2 against an eleven-column track for a while — the prose here
          already said "four" — so the row ended two columns short and left dead
          space down the right of the page. It does not error, it does not
          misalign anything above or below it, and each card looks correct on
          its own; the only symptom is a gap that reads as a layout that
          forgot to finish. Re-add the numbers whenever either span moves. */}
      <div className="grid min-w-0 gap-4 xl:grid-cols-11">
        <UsageActivity
          activity={activity.data}
          failed={activity.isError}
          onRetry={() => void activity.refetch()}
          retrying={activity.isFetching}
          className="xl:col-span-7"
        />
        <UsageSeries
          series={series.data}
          quotas={quotas.data}
          grouping={grouping}
          onGroupingChange={setGrouping}
          failed={series.isError}
          onRetry={() => void series.refetch()}
          retrying={series.isFetching}
          className="xl:col-span-4"
        />
      </div>

      <div className="grid min-w-0 gap-4 xl:grid-cols-3">
        <UsageBreakdown
          breakdown={models.data}
          failed={models.isError}
          onRetry={() => void models.refetch()}
          retrying={models.isFetching}
        />
        <UsageSessions
          sessions={sessions.data}
          failed={sessions.isError}
          onRetry={() => void sessions.refetch()}
          retrying={sessions.isFetching}
          className="xl:col-span-2"
        />
      </div>

      {/* Its own row for the same reason the Bash card has one: two stacked
          tables, one of them the full list of MCP tools. */}
      <UsageToolSplit
        breakdown={tools.data}
        failed={tools.isError}
        onRetry={() => void tools.refetch()}
        retrying={tools.isFetching}
      />

      {/* A standalone row, not a grid cell — deliberately. A five-column table
          (command, calls, total, average, measurement caveats) needs the full
          card width the row above already spends on Breakdown + Sessions, and
          a card that owns its whole row has no span arithmetic to keep in
          sync with a sibling's. */}
      <UsageBashCommands
        insight={bashCommands.data}
        failed={bashCommands.isError}
        onRetry={() => void bashCommands.refetch()}
        retrying={bashCommands.isFetching}
      />

      <UsageFindings
        findings={findings.data}
        failed={findings.isError}
        onRetry={() => void findings.refetch()}
        retrying={findings.isFetching}
      />
    </main>
  );
}

/** The refresh outcome, in the coverage strip rather than as a toast — it is a
 * statement about the data on screen, not an event. */
function refreshNote(refresh: ReturnType<typeof useRefreshUsage>): string | null {
  if (refresh.isError) return "Refresh failed; the indexed data above is unchanged.";
  if (!refresh.data) return null;
  const { changed_sources: changed, indexed_sources: indexed } = refresh.data;
  return `Refreshed ${indexed} source${indexed === 1 ? "" : "s"}; ${changed} changed.`;
}
