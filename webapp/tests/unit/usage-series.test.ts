import { describe, expect, it } from "vitest";

import {
  MIN_FORECAST_DAYS,
  ceilingLabelPositions,
  groupingFor,
  tickLabel,
  trendLine,
  usageSeriesChart,
} from "@/lib/grove/adapters";
import type { UsageSeriesView } from "@/lib/grove/api";

/**
 * The weekly mix card's arithmetic.
 *
 * The forecast on that card is Grove's own — a least-squares trend — so what
 * matters most here is the cases in which it REFUSES to produce a number. A
 * forecast fitted through two points renders as a confident line, and a reader
 * changes their spending on it.
 *
 * The CEILING arithmetic moved to `components/grove/usage/tokens.ts`, where one
 * estimator now serves both the quota card and this chart; its own suite is
 * `usage-capacity.test.ts`. What stays here is label PLACEMENT, which is about
 * pixels rather than about capacity.
 */

const COVERAGE = {
  sources: [],
  degraded_source_count: 0,
  cost_available: false,
  quota_available: false,
};

function view(
  days: string[],
  groups: { key: string; label?: string; values: (number | null)[] }[],
  extra: Partial<UsageSeriesView> = {},
): UsageSeriesView {
  return {
    metric: "tokens",
    dimension: "account",
    tz: "UTC",
    days,
    groups: groups.map((group) => ({
      key: group.key,
      label: group.label ?? group.key,
      total: group.values.reduce<number | null>(
        (sum, value) => (typeof value === "number" ? (sum ?? 0) + value : sum),
        null,
      ),
      points: days.map((day, index) => ({ day, value: group.values[index] ?? null })),
    })),
    truncated: false,
    coverage: COVERAGE,
    ...extra,
  };
}

// ─── the spine ───────────────────────────────────────────────────────────────

describe("the published day spine", () => {
  it("keeps a day nothing reported, as a null point rather than a zero", () => {
    const chart = usageSeriesChart(view(["2026-08-09", "2026-08-10", "2026-08-11"], [
      { key: "max", values: [10, null, 30] },
    ]));

    expect(chart.rows.map((row) => row.day)).toEqual([
      "2026-08-09",
      "2026-08-10",
      "2026-08-11",
    ]);
    expect(chart.rows[1].s0).toBeNull();
  });

  it("answers empty for an empty response rather than throwing", () => {
    const chart = usageSeriesChart(undefined);
    expect(chart.rows).toEqual([]);
    expect(chart.slots).toEqual([]);
    expect(chart.forecast).toBeNull();
  });

  it("keys its series positionally, so a group named `forecast` cannot collide", () => {
    const chart = usageSeriesChart(view(["2026-08-09"], [{ key: "forecast", values: [5] }]));

    expect(chart.slots[0]).toMatchObject({ key: "s0", groupKey: "forecast" });
    expect(chart.rows[0].s0).toBe(5);
    // The trend is off one measured day, so the reserved column is honestly null
    // rather than carrying the group that happened to share its name.
    expect(chart.rows[0].forecast).toBeNull();
  });

  it("bounds the drawn series and reports the tail it withheld", () => {
    const chart = usageSeriesChart(
      view(
        ["2026-08-09"],
        Array.from({ length: 7 }, (_, index) => ({ key: `m${index}`, values: [10 - index] })),
      ),
    );

    expect(chart.slots).toHaveLength(5);
    expect(chart.hiddenGroups).toBe(2);
  });

  it("counts the daemon's own dropped tail in the stated bound", () => {
    const chart = usageSeriesChart(
      view(["2026-08-09"], [{ key: "m0", values: [1] }], { truncated: true }),
    );
    expect(chart.hiddenGroups).toBe(1);
  });
});

// ─── the forecast ────────────────────────────────────────────────────────────

describe("the forecast", () => {
  it("refuses a trend off one or two points", () => {
    expect(trendLine([5, 9])).toEqual([null, null]);
    expect(trendLine([5, null, null])).toEqual([null, null, null]);
    expect(MIN_FORECAST_DAYS).toBe(3);
  });

  it("fits a straight run exactly and projects the last day", () => {
    expect(trendLine([10, 20, 30])).toEqual([10, 20, 30]);
  });

  it("fits THROUGH the gaps rather than around them, so the axis stays a week", () => {
    // Measured on days 0, 2 and 4 of a five-day window: the fitted line still
    // has a value on the days nothing ran, which is what a trend means.
    const fitted = trendLine([10, null, 20, null, 30]);
    expect(fitted).toEqual([10, 15, 20, 25, 30]);
  });

  it("never forecasts negative usage on a falling trend", () => {
    const fitted = trendLine([100, 50, 10, 0, 0, 0, 0]);
    expect(fitted.every((value) => (value ?? 0) >= 0)).toBe(true);
  });

  it("is flat when every measured day agrees", () => {
    expect(trendLine([7, 7, 7])).toEqual([7, 7, 7]);
  });

  it("is fitted over EVERY group, not just the five drawn", () => {
    const groups = Array.from({ length: 7 }, (_, index) => ({
      key: `m${index}`,
      values: [1, 1, 1],
    }));
    const chart = usageSeriesChart(view(["2026-08-09", "2026-08-10", "2026-08-11"], groups));

    expect(chart.slots).toHaveLength(5);
    // 7 groups × 1 each: a forecast built off the drawn five would read 5.
    expect(chart.forecast).toBeCloseTo(7);
    expect(chart.measuredDays).toBe(3);
  });
});

describe("ceiling label placement", () => {
  it("keeps every label on its default side when the caps are well separated", () => {
    const positions = ceilingLabelPositions([{ perDay: 100 }, { perDay: 500 }]);
    expect(positions).toEqual(["insideTopRight", "insideTopRight"]);
  });

  it("flips one side when two caps are close enough to collide", () => {
    const positions = ceilingLabelPositions([{ perDay: 100 }, { perDay: 104 }]);
    expect(positions).toEqual(["insideTopRight", "insideBottomRight"]);
  });

  it("keeps flipping down a run of close caps rather than piling them all up", () => {
    const positions = ceilingLabelPositions([{ perDay: 100 }, { perDay: 103 }, { perDay: 106 }]);
    expect(positions[0]).not.toBe(positions[1]);
    expect(positions[1]).not.toBe(positions[2]);
  });

  it("gives a run of FOUR close caps four distinct corners", () => {
    // The regression that forced the corner list from two to four. With only
    // two positions the third line landed back on the first corner and the
    // fourth on the second, so a crowded band printed two pairs of overlapping
    // labels — and a facet chart reaches four lines routinely, because one
    // account contributes a session budget, a weekly total and a weekly
    // per-model sub-limit before a second account is even counted.
    const positions = ceilingLabelPositions([
      { perDay: 100 },
      { perDay: 103 },
      { perDay: 106 },
      { perDay: 109 },
    ]);
    expect(new Set(positions).size).toBe(4);
  });

  it("resets to the default corner once the run breaks", () => {
    // A far-apart line does not inherit the crowd's offset — it has room, so
    // it takes the corner every uncrowded label takes.
    const positions = ceilingLabelPositions([{ perDay: 100 }, { perDay: 103 }, { perDay: 900 }]);
    expect(positions[2]).toBe("insideTopRight");
  });
});

// ─── the grouping vocabulary ─────────────────────────────────────────────────

describe("the grouping table", () => {
  it("maps every grouping onto a dimension the daemon already serves", () => {
    expect(groupingFor("account").id).toBe("account");
    expect(groupingFor("model").id).toBe("model");
    expect(groupingFor("tool").id).toBe("tool");
  });

  it("asks for CALLS on the tool grouping, because tokens are not per-tool", () => {
    expect(groupingFor("tool").metric).toBe("tool_calls");
    expect(groupingFor("model").metric).toBe("tokens");
  });

  it("falls back rather than fabricating a dimension for an unknown value", () => {
    expect(groupingFor("plan").id).toBe("account");
  });
});

describe("the axis tick", () => {
  it("is the weekday, and does not depend on the reader's zone", () => {
    expect(tickLabel("2026-08-11")).toBe("Tue");
  });

  it("degrades to the raw value rather than rendering `Invalid Date`", () => {
    expect(tickLabel("not-a-day")).toBe("not-a-day");
  });
});
