import type { UsageSeriesView } from "@/lib/grove/api";

/**
 * The seven-day mix chart's arithmetic — wire shape in, chart props out.
 *
 * Pure by acceptance criterion, not by taste: the forecast is a number on this
 * card a reader could act on and it is Grove's own arithmetic rather than
 * anything a provider published. Keeping it here means it can be argued with in
 * a test instead of read off a rendered chart.
 *
 * Two refusals are the whole design, and each is a way the card would lie:
 *
 * * **A day a group did not report stays `null`.** The daemon publishes it that
 *   way and nothing here fills it in — a zero-height bar claims a measurement.
 * * **No forecast off one or two points.** A straight line through two dots is
 *   arithmetic, not evidence, and it swings wildly with the newest day.
 *
 * **The CEILING arithmetic deliberately does not live here.** It used to —
 * `dailyCeiling`/`accountCeilings` normalized a quota window's
 * `tokens_available_estimate` to a day — and the quota card was inverting the
 * same wire fields separately for its own figures, which is two estimators over
 * one question. It is now one function in `components/grove/usage/tokens.ts`,
 * consumed by the card and the chart alike; this module keeps only the label
 * PLACEMENT, which is about pixels rather than about capacity.
 */

/** The groupings the card's dropdown offers, and the metric each can honestly
 * answer. Each `id` IS the daemon's `UsageDimension`, so the table is a
 * presentation layer over the wire vocabulary rather than a second one. */
export interface SeriesGrouping {
  readonly id: "account" | "model" | "tool";
  readonly label: string;
  /** The daemon's `UsageMetric`.
   *
   * `tool` takes `tool_calls` rather than `tokens` deliberately: tokens are
   * reported on generations and nothing attributes them to a tool, so a
   * tokens-by-tool series would be empty on every host. A card that answered
   * "0 tokens" for every tool would be inventing an attribution the transcripts
   * do not carry. */
  readonly metric: "tokens" | "tool_calls";
  /** The unit, for the axis note and the footer. Tertiary tier at the call
   * site — a unit never competes with its figure. */
  readonly unit: string;
}

export const SERIES_GROUPINGS: readonly SeriesGrouping[] = [
  { id: "account", label: "Subscription plan", metric: "tokens", unit: "tokens" },
  { id: "model", label: "Model", metric: "tokens", unit: "tokens" },
  { id: "tool", label: "Agent tool", metric: "tool_calls", unit: "calls" },
];

/** Where the dropdown starts, and where "empty after filtering" offers to go
 * back to — spend per plan is the question the card exists for. */
export const DEFAULT_GROUPING: SeriesGrouping["id"] = "account";

export function groupingFor(id: string): SeriesGrouping {
  return SERIES_GROUPINGS.find((grouping) => grouping.id === id) ?? SERIES_GROUPINGS[0];
}

/**
 * How many stacked series the card draws.
 *
 * Five because the identity palette is `--chart-1…5` and a sixth series would
 * have to reach for a colour the design system has not defined. The tail is
 * reported rather than dropped silently — see `UsageSeriesChart.hiddenGroups`.
 */
export const MAX_SERIES = 5;

/** The fewest measured days a trend may be fitted through. Two points always
 * fit a line perfectly and tell you nothing about whether it is a trend. */
export const MIN_FORECAST_DAYS = 3;

const SECONDS_PER_DAY = 86_400;

export interface SeriesSlot {
  /** The recharts `dataKey`. Positional (`s0`…) rather than the group's own key
   * so a group named `day`, `label` or `forecast` cannot collide with a
   * reserved column of the row. */
  readonly key: string;
  readonly groupKey: string;
  readonly label: string;
}

/** One x-category. Reserved keys are `day` (the ISO date), `label` (the tick)
 * and `forecast`; every other key is a `SeriesSlot.key`. */
export type SeriesChartRow = Record<string, string | number | null>;

export interface UsageSeriesChart {
  readonly rows: readonly SeriesChartRow[];
  readonly slots: readonly SeriesSlot[];
  /** Groups measured in the window but not drawn — the stated bound. Counts the
   * daemon's own dropped tail too, so "showing 5 of 12" is the whole truth. */
  readonly hiddenGroups: number;
  /** The trend's value on the last day of the window, or `null` when too few
   * days were measured to fit one. */
  readonly forecast: number | null;
  /** How many days carried a measurement — the forecast's own evidence, shown
   * beside it so a reader can discount a trend fitted through three days. */
  readonly measuredDays: number;
}

/**
 * The chart's rows, its series and its forecast, from one daemon response.
 *
 * The `days` spine is taken verbatim as the x-axis. Building one from the
 * points instead would close the chart's gaps up, so a week with a quiet Sunday
 * would render as six days and read as a shorter week.
 */
export function usageSeriesChart(view: UsageSeriesView | undefined): UsageSeriesChart {
  const days = view?.days ?? [];
  const groups = view?.groups ?? [];
  const shown = groups.slice(0, MAX_SERIES);
  const slots = shown.map((group, index) => ({
    key: `s${index}`,
    groupKey: group.key,
    label: group.label,
  }));

  // Across EVERY group, not just the drawn ones: the trend is about the whole
  // window's load, and a capped legend must not quietly change the forecast.
  const totals = days.map((_day, index) => dayTotal(groups, index));
  const fitted = trendLine(totals);

  const rows = days.map((day, index) => {
    const row: SeriesChartRow = { day, label: tickLabel(day), forecast: fitted[index] };
    for (const [slot, group] of slots.map((s, i) => [s, shown[i]] as const)) {
      row[slot.key] = group.points[index]?.value ?? null;
    }
    return row;
  });

  return {
    rows,
    slots,
    hiddenGroups: Math.max(0, groups.length - shown.length) + (view?.truncated ? 1 : 0),
    forecast: fitted.at(-1) ?? null,
    measuredDays: totals.filter((value) => value !== null).length,
  };
}

/**
 * The whole window's load on one day, or `null` when no group reported.
 *
 * Sums what was measured rather than refusing on the first `null`: a group's
 * absent day means *this group did not run*, which is a real zero contribution
 * to the day's total, unlike an unmeasured session — the daemon has already
 * nulled a day whose sessions could not be totalled.
 */
function dayTotal(groups: UsageSeriesView["groups"], index: number): number | null {
  const measured = groups
    .map((group) => group.points[index]?.value)
    .filter((value): value is number => typeof value === "number");
  return measured.length > 0 ? measured.reduce((total, value) => total + value, 0) : null;
}

/**
 * A least-squares line through the measured days, evaluated at every day.
 *
 * `null` at every index when fewer than `MIN_FORECAST_DAYS` days were measured
 * — a line through two points is a definition, not a forecast, and it would
 * swing across the whole card as the newest day landed. Negative fitted values
 * clamp to zero: a falling trend can reach no usage, never anti-usage.
 */
export function trendLine(values: readonly (number | null)[]): (number | null)[] {
  const points = values
    .map((value, index) => ({ index, value }))
    .filter((point): point is { index: number; value: number } => typeof point.value === "number");
  if (points.length < MIN_FORECAST_DAYS) return values.map(() => null);

  const meanIndex = points.reduce((sum, p) => sum + p.index, 0) / points.length;
  const meanValue = points.reduce((sum, p) => sum + p.value, 0) / points.length;
  const variance = points.reduce((sum, p) => sum + (p.index - meanIndex) ** 2, 0);
  // Every measurement on one day: the honest line through it is flat.
  const slope = variance === 0 ? 0 : points.reduce(
    (sum, p) => sum + (p.index - meanIndex) * (p.value - meanValue),
    0,
  ) / variance;
  const intercept = meanValue - slope * meanIndex;
  return values.map((_value, index) => Math.max(0, slope * index + intercept));
}

/**
 * Where each ceiling's label sits relative to its own line, so limits that are
 * close in value do not print their labels on top of each other.
 *
 * Two lines that are close in DATA units land pixel-adjacent on the chart
 * whatever its actual height, so "close" is relative to the values themselves
 * rather than to a fixed token count. Walking the ceilings in sorted order and
 * advancing to the next corner on every close pair separates a run of
 * near-equal limits.
 *
 * **The corner list grew from two to four when the chart moved from one line
 * per ACCOUNT to one per FACET.** Two positions separate a close pair and no
 * more: a third line inside the same band landed back on the first corner. A
 * facet chart routinely draws three or four lines (a session budget, a weekly
 * total and a weekly per-model sub-limit share one account), which is exactly
 * the run of near-equal values two corners cannot resolve. Left corners are
 * last because a left-hand label crowds the y-axis; they are the fallback for
 * a crowd, not the default.
 */
const CEILING_LABEL_COLLISION_RATIO = 0.08;

export type CeilingLabelPosition =
  | "insideTopRight"
  | "insideBottomRight"
  | "insideTopLeft"
  | "insideBottomLeft";

const CEILING_LABEL_CORNERS: readonly CeilingLabelPosition[] = [
  "insideTopRight",
  "insideBottomRight",
  "insideTopLeft",
  "insideBottomLeft",
];

export function ceilingLabelPositions(
  ceilings: readonly { perDay: number }[],
): readonly CeilingLabelPosition[] {
  const positions: CeilingLabelPosition[] = ceilings.map(() => CEILING_LABEL_CORNERS[0]);
  const order = ceilings
    .map((ceiling, index) => ({ index, perDay: ceiling.perDay }))
    .sort((a, b) => a.perDay - b.perDay);

  // Counts consecutive close lines rather than merely alternating, so the
  // third and fourth members of one crowded band each get their own corner
  // instead of colliding with the first and second.
  let run = 0;
  for (let i = 1; i < order.length; i += 1) {
    const previous = order[i - 1];
    const current = order[i];
    const gap = current.perDay - previous.perDay;
    const close = current.perDay > 0 && gap <= current.perDay * CEILING_LABEL_COLLISION_RATIO;
    run = close ? run + 1 : 0;
    positions[current.index] = CEILING_LABEL_CORNERS[run % CEILING_LABEL_CORNERS.length];
  }
  return positions;
}

/**
 * The x-axis tick for a day.
 *
 * A weekday, because the window is a week and "Tue" is both the narrowest and
 * the most readable label a seven-tick axis can carry. Parsed as LOCAL midnight
 * so the weekday is a property of the date string and not of the reader's zone
 * — which is what keeps a server render and a browser render identical.
 */
export function tickLabel(day: string): string {
  const parsed = new Date(`${day}T00:00:00`);
  return Number.isNaN(parsed.getTime())
    ? day
    : parsed.toLocaleDateString("en-US", { weekday: "short" });
}
