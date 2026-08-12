"use client";

import { Fragment } from "react";
import { ChartColumnStackedIcon } from "lucide-react";
import { Bar, CartesianGrid, ComposedChart, Line, ReferenceLine, XAxis, YAxis } from "recharts";

import {
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import {
  DEFAULT_GROUPING,
  SERIES_GROUPINGS,
  ceilingLabelPositions,
  groupingFor,
  usageSeriesChart,
  type SeriesChartRow,
  type SeriesGrouping,
  type SeriesSlot,
} from "@/lib/grove/adapters";
import type { UsageQuotasView, UsageSeriesView } from "@/lib/grove/api";
import { cn } from "@/lib/utils";
import { abbreviate } from "./format";
import { UsageSection } from "./section";
import { facetCeilings, seriesDomainMax, type FacetCeiling } from "./tokens";
import { DERIVED_MARK } from "./window-meter";

/**
 * One facet's ceiling, plus the bar slot it may borrow a colour from.
 *
 * `slot` is `null` for every grouping but `account`, and for an account whose
 * bars are not among the drawn five. A line still draws in that case — the
 * limit is real whether or not this chart happens to bar its owner — it just
 * takes the neutral stroke Grove's own arithmetic uses everywhere else on this
 * card rather than borrowing a colour that would claim the wrong identity.
 */
interface DrawnCeiling {
  readonly ceiling: FacetCeiling;
  readonly slot: SeriesSlot | null;
}

/**
 * The week's mix: what the last seven days went on, and where it is heading.
 *
 * The sibling calendar answers *when* the fleet was busy across a year; this
 * answers *what the money went on* across a week, which is the question a
 * person actually changes their behaviour on. It sits in the calendar's row at
 * two sevenths of its width, so every rule here is about a NARROW column: one
 * control per row, a wrapping legend, no second axis label, and figures that
 * step down rather than bleed out.
 *
 * The dropdown selects a `UsageDimension` the daemon already serves — plan,
 * model or tool — rather than three cards or three routes. The METRIC follows
 * the dimension (see `SERIES_GROUPINGS`) because tokens are reported on
 * generations and nothing attributes them to a tool.
 *
 * Both derived lines are allowed to be absent, and that is the point:
 *
 * * the FORECAST is a least-squares trend and needs three measured days, so a
 *   fresh store draws bars and no line rather than a confident slope through
 *   two dots;
 * * the CEILING is normalized from a quota window's `tokens_available_estimate`
 *   — Grove's own extrapolation from a percentage — so a provider that
 *   publishes no window duration (Claude does not) honestly draws no cap.
 */
export function UsageSeries({
  series,
  quotas,
  grouping,
  onGroupingChange,
  failed,
  onRetry,
  retrying,
  className,
}: {
  series: UsageSeriesView | undefined;
  quotas: UsageQuotasView | undefined;
  grouping: SeriesGrouping["id"];
  onGroupingChange: (grouping: SeriesGrouping["id"]) => void;
  failed: boolean;
  onRetry?: () => void;
  retrying?: boolean;
  className?: string;
}): React.ReactNode {
  const selected = groupingFor(grouping);
  const chart = usageSeriesChart(series);
  const config = chartConfig(chart.slots);
  const isAccountGrouping = selected.id === "account";
  // A ceiling is a TOKEN budget; a call count has no plan cap, so the lines
  // are absent for the tool grouping by construction rather than by a null
  // check.
  const ceilings = selected.metric === "tokens" ? facetCeilings(quotas?.accounts) : null;
  // EVERY facet, not one per account: `session`, `weekly_all` and
  // `weekly_scoped` are three independent limits over the same spend, and the
  // one a reader hits first is routinely not the longest. Colour is borrowed
  // from the owning account's bars only under `account`, where such a bar
  // exists to borrow from.
  //
  // **The exact comparison is the `account` grouping's**, where a stack is one
  // account's day and a line is that account's own limit. Under `model` the
  // stack is the whole fleet's day while each line is still ONE account's
  // limit, so with several billing accounts a line understates what the stack
  // is spending against. The lines stay anyway: a limit is a true fact about
  // the subscription whatever these bars are grouped by, the label names its
  // owner as soon as more than one contributes, and the honest alternative —
  // summing facets — is forbidden, because these are independent limits and
  // not parts of a whole.
  const drawnCeilings: DrawnCeiling[] = (ceilings?.ceilings ?? []).map((ceiling) => ({
    ceiling,
    slot:
      (isAccountGrouping &&
        chart.slots.find((candidate) => candidate.groupKey === ceiling.accountId)) ||
      null,
  }));
  const ceilingLabelSides = ceilingLabelPositions(drawnCeilings.map((line) => line.ceiling));
  // The whole point of drawing the lines: a bar you cannot see beside its own
  // limit answers nothing, so the axis is scaled to hold both.
  const domainMax = seriesDomainMax(
    stackMax(chart.rows, chart.slots),
    drawnCeilings.map((line) => line.ceiling),
  );
  const measured = chart.slots.length > 0;

  return (
    <UsageSection
      icon={<ChartColumnStackedIcon />}
      title="Weekly mix"
      description={`Last 7 days, by ${selected.label.toLowerCase()}`}
      failed={failed}
      onRetry={onRetry}
      retrying={retrying}
      failure="The weekly mix could not be loaded."
      loading={!series}
      className={className}
      data-testid="usage-series"
    >
      {/* Its own row, full width. In a two-sevenths column a control beside a
          title is a control that truncates its own label. */}
      <Select value={grouping} onValueChange={(value) => onGroupingChange(asGrouping(value))}>
        <SelectTrigger size="sm" className="w-full" aria-label="Group the week by">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {SERIES_GROUPINGS.map((option) => (
            <SelectItem key={option.id} value={option.id}>
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      {measured ? (
        <>
          {/* GROWS rather than sitting at a fixed 192px. This card shares a grid
              row with the year calendar, which is much taller, so a fixed height
              left most of the column empty — the chart is the one element here
              that can honestly use the extra room, since the dropdown, legend
              and notes are all sized by their own content.

              `min-h-48` keeps the old height as a FLOOR, so the chart never
              collapses when the row is short (a narrow viewport where the two
              cards stack, or a calendar that failed to load). `aspect-auto`
              stays: the vendored container is aspect-ratio-driven by default,
              which would fight a flexed height. */}
          <ChartContainer config={config} className="aspect-auto min-h-48 w-full flex-1">
            <ComposedChart data={chart.rows} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
              <CartesianGrid vertical={false} />
              <XAxis dataKey="label" tickLine={false} axisLine={false} tickMargin={6} />
              <YAxis
                width={44}
                tickLine={false}
                axisLine={false}
                // STATED once rather than left to each `ReferenceLine`'s own
                // `ifOverflow="extendDomain"`: with several ceilings that
                // moves the domain per line and leaves the ticks re-derived
                // from whichever moved it last, so the bars and the lines are
                // only guaranteed to share a scale if the top is set here.
                // `undefined` restores recharts' own fit, which is the honest
                // answer when nothing resolved a ceiling.
                domain={domainMax === null ? undefined : [0, domainMax]}
                tickFormatter={(value: number) => abbreviate(value)}
              />
              {chart.slots.map((slot, index) => (
                <Bar
                  key={slot.key}
                  dataKey={slot.key}
                  stackId="mix"
                  fill={`var(--color-${slot.key})`}
                  radius={index === chart.slots.length - 1 ? [2, 2, 0, 0] : 0}
                />
              ))}
              {chart.forecast === null ? null : (
                <Line
                  dataKey="forecast"
                  type="linear"
                  dot={false}
                  strokeWidth={2}
                  strokeDasharray="4 4"
                  stroke="var(--color-forecast)"
                />
              )}
              {/* One THICK dashed line per FACET, across every configured
                  profile — the comparison this card exists for: a bar, a
                  forecast and every limit that bar is spending against, on one
                  scale. Dashed because the line is Grove's own arithmetic, the
                  same claim the dashed rule under a derived figure makes on
                  the quota card. */}
              {drawnCeilings.map(({ ceiling, slot }, index) => (
                <ReferenceLine
                  key={`${ceiling.accountId}-${ceiling.facetLabel}`}
                  y={ceiling.perDay}
                  stroke={slot ? `var(--color-${slot.key})` : "var(--muted-foreground)"}
                  strokeWidth={2.5}
                  strokeDasharray="2 3"
                  label={{
                    // The facet's own name, not "cap": with several lines an
                    // unnamed one is a stroke a reader cannot attribute, and
                    // the coverage denominator below says how many limits are
                    // missing rather than qualifying each mark separately.
                    value: ceiling.label,
                    position: ceilingLabelSides[index],
                    fontSize: 11,
                    fill: slot ? `var(--color-${slot.key})` : "var(--muted-foreground)",
                  }}
                />
              ))}
              {/* The vendored default prints `value.toLocaleString()`, so a day
                  of tokens arrives as "1,284,553,901" — nine glyphs of precision
                  nobody reads, in a tooltip whose whole job is a glance. The
                  formatter is the vendored seam for exactly this; it replaces
                  the WHOLE row though, indicator included, so the swatch is
                  recomposed here rather than lost. Without it a five-series
                  stack lists five numbers with nothing tying them to their
                  bars. */}
              <ChartTooltip
                content={
                  <ChartTooltipContent
                    formatter={(value, name, item) => (
                      <>
                        {/* Square, not the vendored 2px radius: a radius utility
                            under components/grove fails `lint:styling`, and the
                            colour rides an inline custom property rather than a
                            palette class for the same reason. */}
                        <span
                          className="size-2.5 shrink-0 bg-(--swatch)"
                          style={{ "--swatch": item.color } as React.CSSProperties}
                        />
                        <div className="flex flex-1 items-center justify-between gap-3 leading-none">
                          <span className="min-w-0 truncate text-content-tertiary">{name}</span>
                          <span className="font-mono font-medium tabular-nums">
                            {abbreviate(typeof value === "number" ? value : null)}
                          </span>
                        </div>
                      </>
                    )}
                  />
                }
              />
              {/* Wrapping is the correct answer in this column: a model id is
                  long, and an extra legend row is a cost the card can pay
                  where bleeding past its edge is not.

                  `gap-y-1` is the fix for a legend that looked loosely spaced:
                  the vendored row sets `gap-4`, which applies to BOTH axes, so
                  the moment it wraps every extra line inherits a 16px gutter
                  meant for horizontal separation between items. Wrapped rows of
                  one-line labels need far less. */}
              <ChartLegend content={<ChartLegendContent className="flex-wrap gap-x-4 gap-y-1" />} />
            </ComposedChart>
          </ChartContainer>

          <SeriesNotes
            forecast={chart.forecast}
            measuredDays={chart.measuredDays}
            drawnCeilings={drawnCeilings}
            candidates={ceilings?.candidates ?? 0}
            unit={selected.unit}
            capped={chart.hiddenGroups > 0}
            grouping={selected}
          />
        </>
      ) : grouping === DEFAULT_GROUPING ? (
        <p className="text-sm text-content-tertiary" data-testid="usage-series-empty">
          Not measured: nothing was indexed for the last 7 days.
        </p>
      ) : (
        // A DIFFERENT state from "no data": the store has something, this
        // grouping does not. Without a way back the dropdown is a trap.
        <div className="flex flex-col items-start gap-2" data-testid="usage-series-filtered">
          <p className="text-sm text-content-tertiary">
            No {selected.label.toLowerCase()} was recorded in the last 7 days.
          </p>
          <Button variant="outline" size="sm" onClick={() => onGroupingChange(DEFAULT_GROUPING)}>
            Show subscription plan
          </Button>
        </div>
      )}
    </UsageSection>
  );
}

/**
 * The two derived lines, said in words.
 *
 * Words rather than only lines because §4.7 forbids colour as a sole carrier,
 * and because a dashed line is meaningless until something says what it is
 * forecasting. An absent line says so here rather than leaving a reader to
 * notice a missing stroke.
 *
 * Structural rows, never one wrapping sentence: this column is narrow, and a
 * paragraph that reflows is where a card starts pushing its own edge.
 */
function SeriesNotes({
  forecast,
  measuredDays,
  drawnCeilings,
  candidates,
  unit,
  capped,
  grouping,
}: {
  forecast: number | null;
  measuredDays: number;
  /** Every facet limit drawn, in the order the lines were drawn, so a row
   * lines up with its stroke by position. */
  drawnCeilings: readonly DrawnCeiling[];
  /** Every (account, facet) pair that had quota to report, drawn or not — the
   * denominator for "N of M limits". */
  candidates: number;
  unit: string;
  capped: boolean;
  grouping: SeriesGrouping;
}): React.ReactNode {
  return (
    // `border-t` reads this as a SECOND region rather than the chart's own
    // trailing rows — the same rule (a tint plus a rule) `SectionCard` draws
    // between its header and body, one level down. Without it the legend, the
    // dashed forecast and these words all read as one undifferentiated stack.
    <dl className="grid grid-cols-[auto_minmax(0,1fr)] items-baseline gap-x-3 gap-y-1 border-t border-border pt-2">
      <dt className="text-xs text-content-tertiary">Forecast</dt>
      <dd className="min-w-0 text-xs tabular-nums">
        {forecast === null ? (
          <span className="text-content-tertiary">
            not measured — a trend needs 3 days, {measuredDays} measured
          </span>
        ) : (
          <>
            {/* Derived, and marked as such: a least-squares trend through the
                measured days is the most obviously calculated figure on the
                card, so leaving it plain would make the mark mean "some of
                Grove's arithmetic" rather than all of it. */}
            <span
              className={cn(DERIVED_MARK, "text-content-primary")}
              title={`Grove's least-squares trend through ${measuredDays} measured days, read at the last day of the window.`}
              data-derived="true"
            >
              {abbreviate(Math.round(forecast))}
            </span>{" "}
            <span className="text-content-tertiary">
              {unit}/day over {measuredDays} measured days
            </span>
          </>
        )}
      </dd>

      {drawnCeilings.length === 0 ? (
        <>
          <dt className="text-xs text-content-tertiary">Limits</dt>
          <dd className="min-w-0 text-xs text-content-tertiary">
            {grouping.metric !== "tokens"
              ? "not applicable to call counts"
              : // "None of the 3 limits your plans DO report could be sized" is
                // a materially different message from "you have no limits", and
                // only the second is what a bare refusal says. The denominator
                // is the same one the coverage row states when some are drawn.
                candidates > 0
                ? `not measured — none of ${candidates} limits could be sized`
                : "not measured — no plan publishes a token budget"}
          </dd>
        </>
      ) : (
        <>
          {/* One row per FACET, each keyed to its own line's colour, rather
              than one blended figure — a reader compares ONE limit's line
              against the bars beneath it, never against a sum of every limit
              at once. The limits are not parts of a whole and must never be
              added together. */}
          {drawnCeilings.map(({ ceiling, slot }) => (
            <Fragment key={`${ceiling.accountId}-${ceiling.facetLabel}`}>
              <dt
                className="flex max-w-24 min-w-0 items-center gap-1.5 text-xs text-content-tertiary"
                title={`${ceiling.accountLabel} · ${ceiling.facetLabel}`}
              >
                {/* Square, per the tooltip swatch above: a radius utility
                    under components/grove fails `lint:styling`. */}
                <span
                  aria-hidden
                  className="size-2 shrink-0 bg-(--swatch)"
                  style={
                    {
                      "--swatch": slot ? `var(--color-${slot.key})` : "var(--muted-foreground)",
                    } as React.CSSProperties
                  }
                />
                <span className="min-w-0 truncate">{ceiling.label}</span>
              </dt>
              <dd className="min-w-0 text-xs tabular-nums">
                {/* Marked with the same dashed rule the quota card puts under
                    a derived figure: every one of these is Grove normalizing
                    an estimate it inverted from a percentage, and a per-day
                    figure carries two divisions a reader cannot see. */}
                <span
                  className={cn(DERIVED_MARK, "text-content-primary")}
                  title={ceilingTitle(ceiling, unit)}
                  data-derived="true"
                >
                  {abbreviate(Math.round(ceiling.perDay))}
                </span>{" "}
                <span className="text-content-tertiary">{unit}/day</span>
              </dd>
            </Fragment>
          ))}
          {/* A facet at 0% used can never yield an estimate — two of this
              host's four live windows sit there — so partial coverage is the
              ordinary case, not an error, and the denominator says so
              plainly. */}
          {drawnCeilings.length < candidates ? (
            <>
              <dt className="text-xs text-content-tertiary">Limit coverage</dt>
              <dd className="min-w-0 text-xs text-content-tertiary">
                {drawnCeilings.length} of {candidates} limits
              </dd>
            </>
          ) : null}
        </>
      )}

      {capped ? (
        <>
          <dt className="text-xs text-content-tertiary">Showing</dt>
          <dd className="min-w-0 text-xs text-content-tertiary">the 5 largest groups</dd>
        </>
      ) : null}
    </dl>
  );
}

/**
 * The recharts colour contract, built from the identity palette.
 *
 * `--chart-1…5` is the design system's IDENTITY set — a fixed property of the
 * thing, which is exactly what a model or a plan is — and `ChartConfig` is the
 * vendored mechanism for reaching it, so no Grove element ever carries a colour
 * class. The forecast is NEUTRAL on purpose: it is Grove's own arithmetic
 * rather than a thing with an identity or a state, and a hue gated on nothing
 * is an identity colour pretending to be a state one.
 */
function chartConfig(slots: readonly { key: string; label: string }[]): ChartConfig {
  const config: ChartConfig = {
    forecast: { label: "Forecast", color: "var(--muted-foreground)" },
  };
  slots.forEach((slot, index) => {
    config[slot.key] = { label: slot.label, color: `var(--chart-${index + 1})` };
  });
  return config;
}

function asGrouping(value: string): SeriesGrouping["id"] {
  return groupingFor(value).id;
}

/**
 * The tallest day, summed across the stack.
 *
 * The bars are stacked, so the value the axis has to clear is a day's TOTAL
 * and not its largest slot — sizing to the slot leaves the top of every stack
 * drawn outside the plot. `null` when no day measured anything, which lets
 * `seriesDomainMax` fall back to the ceilings alone.
 */
function stackMax(
  rows: readonly SeriesChartRow[],
  slots: readonly SeriesSlot[],
): number | null {
  let max: number | null = null;
  for (const row of rows) {
    let total: number | null = null;
    for (const slot of slots) {
      const value = row[slot.key];
      if (typeof value === "number") total = (total ?? 0) + value;
    }
    if (total !== null && (max === null || total > max)) max = total;
  }
  return max;
}

/** What the per-day figure was computed from — two divisions a reader cannot
 * see, named so the number can be checked rather than taken. */
function ceilingTitle(ceiling: FacetCeiling, unit: string): string {
  const whose =
    ceiling.basis === "published"
      ? "the provider's own published limit"
      : "Grove's estimate, inverted from the percentage the provider reported";
  return `${ceiling.accountLabel} · ${ceiling.facetLabel}: ${whose}, normalized to ${unit} per day over the window's own duration.`;
}
