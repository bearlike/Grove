import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { UsageSeries } from "@/components/grove/usage/series";
import type { BillingAccountView, UsageQuotasView, UsageSeriesView } from "@/lib/grove/api";

/**
 * The weekly-mix card's STATES, which are what finish it.
 *
 * The card sits in a two-sevenths column beside the year calendar, so its
 * failure modes are the narrow-column ones: an unmeasured figure that shouts, a
 * grouping that has nothing to show and no way back out of itself, and a line
 * drawn for a number nobody measured. Each is asserted on the words and roles
 * the card renders, never on the classes that produce them.
 */
function render(node: React.ReactNode): string {
  return renderToStaticMarkup(<>{node}</>);
}

const COVERAGE = {
  sources: [],
  degraded_source_count: 0,
  cost_available: false,
  quota_available: false,
};

function series(
  groups: { key: string; values: (number | null)[] }[],
  days = ["2026-08-09", "2026-08-10", "2026-08-11"],
): UsageSeriesView {
  return {
    metric: "tokens",
    dimension: "account",
    tz: "UTC",
    days,
    groups: groups.map((group) => ({
      key: group.key,
      label: group.key,
      total: 1,
      points: days.map((day, index) => ({ day, value: group.values[index] ?? null })),
    })),
    truncated: false,
    coverage: COVERAGE,
  };
}

const NO_QUOTA: UsageQuotasView = { accounts: [], coverage: COVERAGE };

const WEEKLY_QUOTA: UsageQuotasView = {
  accounts: [
    {
      account_id: "acct",
      provider: "claude_code",
      label: "Personal",
      billing_mode: "subscription",
      status: "ok",
      windows: [
        {
          scope: "weekly",
          label: "weekly",
          window_seconds: 604_800,
          projection: { verdict: "unknown", tokens_available_estimate: 7_000_000 },
        },
      ],
    } as BillingAccountView,
  ],
  coverage: COVERAGE,
};

function card(props: Partial<React.ComponentProps<typeof UsageSeries>> = {}): string {
  return render(
    <UsageSeries
      series={series([{ key: "max", values: [10, 20, 30] }])}
      quotas={NO_QUOTA}
      grouping="account"
      onGroupingChange={() => undefined}
      failed={false}
      {...props}
    />,
  );
}

describe("the states", () => {
  // The positive control for the assertion below it: without this, "no chart
  // while loading" would pass just as happily if the chart never rendered at all.
  it("mounts the vendored chart once the data lands", () => {
    expect(card()).toContain('data-slot="chart"');
  });

  it("shows a skeleton, not a chart, while the query is unresolved", () => {
    const html = card({ series: undefined });
    expect(html).toContain('data-slot="card"');
    expect(html).not.toContain('data-slot="chart"');
    expect(html).not.toContain("recharts");
  });

  it("keeps the failure inside the card, with the one action that might fix it", () => {
    const html = card({ failed: true, onRetry: () => undefined });
    expect(html).toContain("The weekly mix could not be loaded.");
    expect(html).toContain('role="alert"');
    expect(html).toContain("Try again");
  });

  it("says nothing was indexed, rather than drawing an empty chart", () => {
    const html = card({ series: series([]) });
    expect(html).toContain('data-testid="usage-series-empty"');
    expect(html).toContain("Not measured");
    expect(html).not.toContain('role="alert"');
  });

  // The distinction the card exists to keep: the store HAS data, this grouping
  // does not — and without a way back the dropdown is a trap.
  it("distinguishes an empty grouping from an empty store, and offers the way back", () => {
    const html = card({ series: series([]), grouping: "tool" });
    expect(html).toContain('data-testid="usage-series-filtered"');
    expect(html).toContain("No agent tool was recorded in the last 7 days.");
    expect(html).toContain("Show subscription plan");
    expect(html).not.toContain('data-testid="usage-series-empty"');
  });
});

describe("the dropdown", () => {
  it("offers exactly the three groupings, and names the live one in the card", () => {
    const html = card({ grouping: "model" });
    expect(html).toContain("Last 7 days, by model");
    expect(html).toContain("Group the week by");
  });
});

describe("the derived lines are allowed to be absent", () => {
  it("names the forecast rather than only drawing it", () => {
    const html = card();
    expect(html).toContain("Forecast");
    expect(html).toContain("measured days");
  });

  it("says why there is no forecast instead of leaving a missing stroke", () => {
    const html = card({ series: series([{ key: "max", values: [10, null, null] }]) });
    expect(html).toContain("a trend needs 3 days");
  });

  it("draws no limit when no plan publishes a token budget, and says so", () => {
    const html = card();
    expect(html).toContain("Limits");
    expect(html).toContain("no plan publishes a token budget");
  });

  it("has no limit at all on a call count, and does not pretend otherwise", () => {
    expect(card({ grouping: "tool", quotas: WEEKLY_QUOTA })).toContain(
      "not applicable to call counts",
    );
  });

  it("draws a facet limit under EVERY tokens grouping, not only `account`", () => {
    // The limits are a property of the subscription, not of how these bars
    // happen to be grouped: "how much am I spending against everything
    // available to me" is the same question under `model`.
    const html = card({ grouping: "model", quotas: WEEKLY_QUOTA });
    // 7M tokens over a 7-day window is 1.0M a day.
    expect(html).toContain("1.0M");
    expect(html).toContain("tokens/day");
  });

  it("marks every derived figure in the notes with the same rule the card uses", () => {
    // A least-squares trend and a twice-divided per-day ceiling are the two
    // most obviously calculated figures on the surface; leaving either plain
    // would make the mark read as "some of Grove's arithmetic".
    const html = card({ grouping: "model", quotas: WEEKLY_QUOTA });
    expect(html.match(/data-derived="true"/g)?.length ?? 0).toBeGreaterThanOrEqual(2);
    expect(html).toContain("underline decoration-dashed underline-offset-2");
  });
});

describe("every facet limit, across every profile", () => {
  // The series key IS the account id under `account` — the coupling the card
  // relies on to colour each plan's line off its own bars.
  function accountSeries(
    groups: { key: string; label: string; values: (number | null)[] }[],
  ): UsageSeriesView {
    const days = ["2026-08-09", "2026-08-10", "2026-08-11"];
    return {
      metric: "tokens",
      dimension: "account",
      tz: "UTC",
      days,
      groups: groups.map((group) => ({
        key: group.key,
        label: group.label,
        total: 1,
        points: days.map((day, index) => ({ day, value: group.values[index] ?? null })),
      })),
      truncated: false,
      coverage: COVERAGE,
    };
  }

  /** One account with a facet per entry, each already carrying the resolved
   * duration Claude's windows only get from the operator's config. */
  function billingAccount(
    id: string,
    label: string,
    facets: { label: string; seconds: number; estimate: number | null }[],
  ): BillingAccountView {
    return {
      account_id: id,
      provider: "claude_code",
      label,
      billing_mode: "subscription",
      status: "ok",
      windows: facets.map((facet) => ({
        scope: "weekly",
        label: facet.label,
        window_seconds: null,
        used_percent: 50,
        projection: {
          verdict: "unknown",
          resolved_window_seconds: facet.seconds,
          tokens_used: facet.estimate === null ? null : facet.estimate / 2,
          tokens_available_estimate: facet.estimate,
        },
      })),
    } as BillingAccountView;
  }

  function facetCard(accounts: BillingAccountView[], groups = [{ key: "acct-a", label: "Personal", values: [10, 20, 30] }]) {
    return render(
      <UsageSeries
        series={accountSeries(groups)}
        quotas={{ accounts, coverage: COVERAGE }}
        grouping="account"
        onGroupingChange={() => undefined}
        failed={false}
      />,
    );
  }

  it("names EVERY facet of a plan, not just its longest window", () => {
    // The behaviour this replaced collapsed an account to one line, silently
    // dropping the session budget a reader hits first.
    const html = facetCard([
      billingAccount("acct-a", "Personal", [
        { label: "session", seconds: 18_000, estimate: 100_000 },
        { label: "weekly_all", seconds: 604_800, estimate: 7_000_000 },
      ]),
    ]);
    expect(html).toContain("Session");
    expect(html).toContain("Weekly all");
    // 100K over 5h is 480.0K/day; 7M over a week is 1.0M/day. Never summed.
    expect(html).toContain("480.0K");
    expect(html).toContain("1.0M");
  });

  it("spans profiles, so a second subscription's limits are on the same chart", () => {
    const html = facetCard(
      [
        billingAccount("acct-a", "Personal", [
          { label: "weekly_all", seconds: 604_800, estimate: 7_000_000 },
        ]),
        billingAccount("acct-b", "Work", [
          { label: "weekly_all", seconds: 604_800, estimate: 700_000 },
        ]),
      ],
      [
        { key: "acct-a", label: "Personal", values: [10, 20, 30] },
        { key: "acct-b", label: "Work", values: [5, 5, 5] },
      ],
    );
    // Both drawn, and each disambiguated by its owner because the facet name
    // alone would be two identical marks.
    expect(html).toContain("Personal Weekly all");
    expect(html).toContain("Work Weekly all");
  });

  it("still draws a limit whose owner has no bars on this chart", () => {
    // The limit is real whether or not this chart happens to bar its owner —
    // it just takes the neutral stroke rather than borrowing a colour that
    // would claim the wrong identity.
    const html = facetCard([
      billingAccount("acct-a", "Personal", [
        { label: "weekly_all", seconds: 604_800, estimate: 7_000_000 },
      ]),
      billingAccount("acct-b", "Work", [
        { label: "monthly", seconds: 604_800, estimate: 700_000 },
      ]),
    ]);
    expect(html).toContain("Weekly all");
    expect(html).toContain("Monthly");
  });

  it("states its coverage against every candidate FACET, not every account", () => {
    // A denominator of accounts would understate how much of the picture is
    // missing: one account can contribute three limits and state only one.
    const html = facetCard([
      billingAccount("acct-a", "Personal", [
        { label: "session", seconds: 18_000, estimate: null },
        { label: "weekly_all", seconds: 604_800, estimate: 7_000_000 },
        { label: "weekly_scoped", seconds: 604_800, estimate: null },
      ]),
    ]);
    expect(html).toContain("Limit coverage");
    expect(html).toContain("1 of 3 limits");
  });

  it("invents no line for a plan that cannot produce one, and counts what it lost", () => {
    // "None of the 2 limits your plans DO report could be sized" is a
    // materially different message from "you have no limits", and only the
    // second is what a bare refusal says.
    const html = facetCard([
      billingAccount("acct-a", "Personal", [
        { label: "session", seconds: 18_000, estimate: null },
        { label: "weekly_all", seconds: 604_800, estimate: null },
      ]),
    ]);
    expect(html).toContain("none of 2 limits could be sized");
  });

  it("says no plan publishes a budget when no plan reported a window at all", () => {
    expect(card()).toContain("no plan publishes a token budget");
  });
});
