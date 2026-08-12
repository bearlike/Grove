"use client";

/**
 * Grove's activity heatmap — a PORT of `components/assistant-ui/heat-graph.tsx`,
 * kept diffable against it line for line.
 *
 * WHY a port and not a composition: the vendored component's only prop is
 * `data`, and it closes over a module-level `COLORS` const. There is no seam to
 * pass a scale through, and its own `HeatGraphPrimitive.Root` takes exactly the
 * `colorScale` this needs — so the smallest honest change is to reproduce the
 * anatomy and hand the primitive a different scale.
 *
 * THE RULE FOR EDITING THIS FILE: only the deltas below may diverge from the
 * vendored original. Everything else — structure, spacing, cell sizing, the
 * tooltip's wording, `rounded-sm`, `shadow-lg` — is upstream's, and a "small
 * improvement" here is how the look drifts.
 *
 *   1. `colorScale` — `HEAT` (the theme's `--heat-*` ramp) instead of the
 *      hard-coded blue `COLORS`. The ramp ends exactly on `--success`, so the
 *      calendar and the headline token figure read as one signal, and dark mode
 *      gets its own five steps because a colour scale is theme data.
 *   2. Semantic tokens for the two raw palette classes: `text-gray-500` →
 *      `text-muted-foreground`, and the tooltip's `bg-gray-900 text-white` →
 *      `bg-popover text-popover-foreground`. Same reason `lint:styling` exists —
 *      a hard-coded grey is a look that drifts from the vendored components
 *      around it, and it is wrong outright in dark mode.
 *   3. The tooltip says what the cell actually counts, in BOTH forms:
 *      "516.8M tokens (516,826,976)". Upstream is GitHub's calendar, so it
 *      reads "516826976 contributions" — the wrong noun for tokens, and a raw
 *      integer nobody can parse at a glance.
 *
 *      This carries both on purpose, and it is the page's one exception. The
 *      rule everywhere else is abbreviated-visible with the exact figure in the
 *      tooltip, which `AbbreviatedNumber` implements at every call site. A
 *      heatmap cell breaks that rule's premise: the cell is a colour swatch
 *      with no visible figure at all, so its tooltip is BOTH the readable
 *      rendering and the only place the exact number exists. Dropping either
 *      one loses something the rest of the page keeps — grouped digits alone
 *      were measured as unreadable by a person, and abbreviating alone would
 *      put the precise figure nowhere. The parenthetical is omitted below a
 *      thousand, where the two forms are identical and it would just stutter.
 *   4. The cell has a MAXIMUM size, and that is a height fix, not a width one.
 *      Upstream's cell is `aspect-square w-full` in a `1fr` column, so its
 *      height is whatever width the container hands it: on a full-row card at
 *      1920 the cells grew to 26px and the seven rows to 203px, and the card
 *      was 140px taller on a wide monitor than on a phone for no added
 *      information. Capping the cell and centring it in its column spends the
 *      surplus width on the GAP instead, which is the one axis that costs no
 *      height. There is no seam for this outside the port: `HeatGraph.Grid`
 *      writes `repeat(N, 1fr)` as an inline style, and the cell's class is
 *      hard-coded here.
 *
 * `components/assistant-ui/heat-graph.tsx` stays in place, unmodified: it is the
 * oracle this file is diffed against, and `registry:check` verifies it.
 */

import * as HeatGraphPrimitive from "heat-graph";

import { abbreviate, exact } from "./format";

const HEAT = [
  "var(--heat-0)",
  "var(--heat-1)",
  "var(--heat-2)",
  "var(--heat-3)",
  "var(--heat-4)",
];

export function ActivityHeatmap({
  data,
}: {
  data: HeatGraphPrimitive.DataPoint[];
}) {
  return (
    <HeatGraphPrimitive.Root
      data={data}
      weekStart="monday"
      colorScale={HEAT}
      className="flex flex-col gap-2"
    >
      <MonthLabels />
      <div className="flex gap-2">
        <DayLabels />
        <CellGrid />
      </div>
      <GraphLegend />
      <CellTooltip />
    </HeatGraphPrimitive.Root>
  );
}

function MonthLabels() {
  return (
    <div className="relative ms-10 h-5">
      <HeatGraphPrimitive.MonthLabels>
        {({ label, totalWeeks }) => (
          <span
            className="absolute text-xs text-muted-foreground"
            style={{ left: `${(label.column / totalWeeks) * 100}%` }}
          >
            {HeatGraphPrimitive.MONTH_SHORT[label.month]}
          </span>
        )}
      </HeatGraphPrimitive.MonthLabels>
    </div>
  );
}

function DayLabels() {
  return (
    <div className="flex w-8 shrink-0 flex-col justify-between py-[2px]">
      <HeatGraphPrimitive.DayLabels>
        {({ label }) => (
          <span className="flex h-[13px] items-center text-xs text-muted-foreground">
            {label.row % 2 === 0
              ? HeatGraphPrimitive.DAY_SHORT[label.dayOfWeek]
              : ""}
          </span>
        )}
      </HeatGraphPrimitive.DayLabels>
    </div>
  );
}

function CellGrid() {
  return (
    <HeatGraphPrimitive.Grid className="flex-1 gap-[3px]">
      {() => (
        <HeatGraphPrimitive.Cell className="mx-auto aspect-square w-full max-w-[14px] rounded-sm" />
      )}
    </HeatGraphPrimitive.Grid>
  );
}

function CellTooltip() {
  return (
    <HeatGraphPrimitive.Tooltip className="pointer-events-none rounded-md bg-popover px-3 py-1.5 text-xs whitespace-nowrap text-popover-foreground shadow-lg">
      {({ cell }) => (
        <>
          <strong>
            {abbreviate(cell.count)} {cell.count === 1 ? "token" : "tokens"}
          </strong>
          {abbreviate(cell.count) === exact(cell.count) ? null : ` (${exact(cell.count)})`}{" "}
          on{" "}
          {cell.date.toLocaleDateString("en-US", {
            month: "short",
            day: "numeric",
            year: "numeric",
          })}
        </>
      )}
    </HeatGraphPrimitive.Tooltip>
  );
}

function GraphLegend() {
  return (
    <div className="ms-auto flex items-center gap-1 text-xs text-muted-foreground">
      <span>Less</span>
      <HeatGraphPrimitive.Legend>
        {() => (
          <HeatGraphPrimitive.LegendLevel className="h-[13px] w-[13px] rounded-sm" />
        )}
      </HeatGraphPrimitive.Legend>
      <span>More</span>
    </div>
  );
}
