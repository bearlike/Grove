import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { UsageActivity } from "@/components/grove/usage/activity";
import { UsageCost } from "@/components/grove/usage/cost";
import { UsageFindings } from "@/components/grove/usage/findings";
import { UsageSessions } from "@/components/grove/usage/sessions";
import { UsageSummary } from "@/components/grove/usage/summary";
import { TooltipProvider } from "@/components/ui/tooltip";
import type {
  UsageActivityView,
  UsageFindingsView,
  UsageSessionPageView,
  UsageSummaryView,
} from "@/lib/grove/api";

/**
 * What each panel renders from the daemon's real shapes.
 *
 * The numbers below are this host's, because the failures worth pinning are the
 * ones a synthetic fixture rounds off: 2,475 findings is what made the page
 * scroll forever, and `tokens: all null` while `tool_calls: 204643` is what
 * made a page of "not measured" out of a store full of measurements.
 */
const EMPTY_COVERAGE = {
  sources: [],
  degraded_source_count: 0,
  cost_available: false,
  quota_available: true,
};

const SUMMARY: UsageSummaryView = {
  tz: "UTC",
  sessions: 513,
  turns: 12591,
  tokens: {
    fresh_input: null,
    cache_read: null,
    cache_creation: null,
    reasoning: null,
    output: null,
    provider_total: null,
  },
  duration: { active_ms: null, elapsed_span_ms: 9829595686, confidence: "derived" },
  tools: { calls: 204643, failures: 3641, distinct_tools: 349 },
  files_changed: 8635,
  cost: null,
  accounts: 2,
  projects: 13,
  coverage: {
    ...EMPTY_COVERAGE,
    sources: [
      {
        source_id: "claude_code-000000000000",
        provider: "claude_code",
        label: ".claude",
        health: "degraded",
        detail: "transcript cwd was not measured",
        session_count: 320,
        last_indexed_at: "2026-08-10T06:15:44Z",
      },
    ],
    degraded_source_count: 1,
    last_refresh_at: "2026-08-10T06:15:44Z",
  },
};

function render(node: React.ReactNode): string {
  return renderToStaticMarkup(<TooltipProvider>{node}</TooltipProvider>);
}

describe("UsageSummary", () => {
  const html = render(<UsageSummary summary={SUMMARY} failed={false} />);

  it("abbreviates every number over a thousand and leaves smaller ones whole", () => {
    expect(html).toContain("204.6K");
    expect(html).toContain("12.6K");
    expect(html).toContain("8.6K");
    expect(html).toContain(">513<");
    expect(html).toContain(">13<");
  });

  it("mounts a tooltip trigger exactly where the value was shortened", () => {
    // 513, 13 and 2 are shown whole, so a tooltip on them would repeat their
    // trigger and cost a keyboard user a stop for nothing.
    expect(html.match(/data-slot="tooltip-trigger"/g)).toHaveLength(4);
  });

  it("is the six totals the daemon reports on every host", () => {
    // Tokens are deliberately not among them, and that is a placement decision,
    // NOT a claim that tokens are unmeasured: they are measured, and the
    // activity card states the real total.
    expect(html).toContain("Tool calls");
    expect(html).not.toContain("Tokens");
  });
});

describe("UsageActivity", () => {
  const activity: UsageActivityView = {
    metric: "tokens",
    tz: "UTC",
    total: 3_884_000_000,
    max_value: 379_601_675,
    buckets: [
      { day: "2026-02-07", value: 320_840_783, sessions: 1 },
      { day: "2026-02-12", value: 379_601_675, sessions: 6 },
      { day: "2026-08-10", value: 3_385_784, sessions: 1 },
    ],
    coverage: EMPTY_COVERAGE,
  };
  const html = render(<UsageActivity activity={activity} failed={false} />);

  it("renders the calendar heatmap, whose anatomy is upstream's", () => {
    // The heatmap is the requirement, so a future "let's just use a line chart"
    // has to argue with a failing test. Legend + a grid of cells.
    expect(html).toContain("Less");
    expect(html).toContain("More");
    expect(html.match(/rounded-sm/g)?.length).toBeGreaterThan(10);
  });

  it("paints the cells from the theme's heat ramp, not the upstream blue", () => {
    // `heat-graph` applies the scale as an inline background-color, so the ramp
    // reaching the DOM is observable — and reverting to the vendored component
    // (whose ramp is `#2563eb`) fails right here rather than at a screenshot.
    expect(html).toContain("background-color:var(--heat-");
    expect(html).not.toContain("#2563eb");
  });

  it("renders the measured total even though the summary aggregate is null", () => {
    expect(html).toContain("3.9B");
    expect(html).not.toContain("not measured");
  });

  it("names the window it asked for, not the span the store happened to hold", () => {
    expect(html).toContain("last 365 days");
  });

  it("fills the card's width while the CELL, not the calendar, bounds the height", () => {
    // These two classes are a pair and the bug lives in swapping either for the
    // other. `min-w-` is a floor that still lets the calendar fill a wide card
    // (a fixed width left a third of it empty at 1920); `max-w-` on the cell is
    // what stops seven `aspect-square` rows growing to 203px on that same wide
    // card. Measured: 266px tall at 1920, 1440 and 390 alike.
    expect(html).toContain("min-w-[56rem]");
    expect(html).toContain("max-w-[14px]");
  });

  it("puts the total in the header, which costs no row of its own", () => {
    const action = html.slice(html.indexOf('data-slot="card-action"'));
    expect(action.slice(0, 400)).toContain("3.9B");
    // …and before the content, i.e. in the header rather than above the grid.
    expect(html.indexOf("3.9B")).toBeLessThan(html.indexOf('data-slot="card-content"'));
  });

  it("says so plainly when the range really has no tokens", () => {
    const empty = render(
      <UsageActivity activity={{ ...activity, buckets: [], total: 0 }} failed={false} />,
    );
    expect(empty).toContain("Not measured: no indexed source reported token counts");
  });
});

describe("UsageCost", () => {
  it("states the absence once, with the reason and the degraded source", () => {
    const html = render(<UsageCost summary={SUMMARY} failed={false} />);
    expect(html.match(/not measured/g)).toHaveLength(1);
    expect(html).toContain("price table");
    expect(html).toContain(".claude is degraded");
  });
});

describe("UsageFindings", () => {
  const findings: UsageFindingsView = {
    coverage: EMPTY_COVERAGE,
    // The daemon now states how many it withheld; `total === findings.length`
    // is the "nothing was capped" signal, so an unsliced fixture says so.
    total: 2475,
    findings: Array.from({ length: 2475 }, (_, index) => ({
      kind: "edit_churn" as const,
      title: `Repeated edits to file-${index}`,
      detail: null,
      count: 362 - index,
      impact: 1,
      confidence: 1,
      first_seen_at: null,
      last_seen_at: null,
      evidence_filters: { tz: "UTC" },
      session_ids: [],
      subject: null,
    })),
  };
  const html = render(<UsageFindings findings={findings} failed={false} />);

  it("caps the DOM at a hundred rows however many the daemon returns", () => {
    expect(html.match(/<li /g)).toHaveLength(100);
  });

  it("admits the cap rather than passing the sample off as the set", () => {
    expect(html).toContain("Showing 100 of");
    expect(html).toContain("2.5K");
  });

  it("bounds its own height so the page cannot grow with the data", () => {
    // The BOUND is the contract, not which scroll container provides it. This
    // used to assert radix's `data-slot="scroll-area"` and so failed the moment
    // the app settled on one scroll idiom — a test pinning the implementation
    // of something it was not testing.
    expect(html).toContain("max-h-72");
    expect(html).toContain("overflow-y-auto");
  });
});

describe("UsageSessions", () => {
  const sessions: UsageSessionPageView = {
    sort: "recent",
    coverage: EMPTY_COVERAGE,
    next_cursor: null,
    rows: [
      {
        session_id: "019fe7d6-1d1c-76a3-825a-f31cdb338cf3",
        provider: "codex",
        cwd: "/repos/owner/repo",
        project: "/repos/owner/repo",
        account_id: "codex-000000000000",
        account_label: ".codex",
        source_id: "codex-000000000000",
        models: ["gpt-5.6-luna"],
        started_at: "2026-08-09T18:46:40Z",
        last_event_at: "2026-08-10T06:13:21Z",
        duration: { active_ms: 16922063, elapsed_span_ms: 41201263, confidence: "derived" },
        turns: 40,
        tool_calls: 1709,
        tool_failures: 0,
        files_changed: 0,
        tokens: {
          fresh_input: null,
          cache_read: 233484160,
          cache_creation: 0,
          reasoning: 152981,
          output: 193830,
          provider_total: 97834653,
        },
        cost: null,
        parser_health: "ok",
        parser_detail: null,
      },
    ],
  };
  const html = render(<UsageSessions sessions={sessions} failed={false} />);

  it("counts the token classes without adding the provider's own total on top", () => {
    // 233_484_160 + 0 + 152_981 + 193_830 = 233.8M. Including provider_total
    // would report 331.7M for the same session.
    expect(html).toContain("233.8M");
  });

  it("shortens the path to what distinguishes two checkouts", () => {
    expect(html).toContain("owner/repo");
  });

  it("bounds its own height", () => {
    expect(html).toContain("max-h-64");
    expect(html).toContain("overflow-y-auto");
  });

  it("gives the project column the leftover width and the numbers only their own", () => {
    // The pair that fixes "ellipsis beside empty space". `max-w-0` alone — how
    // this shipped — asked for zero width, so `bearlike/Grove` clipped in an
    // 82px column while `Last active` held 209px. Measured after: 43 of 50 rows
    // clipped → 4, and the table still fits its card.
    const projectCell = html.slice(html.indexOf('title="/repos/owner/repo"') - 200, html.indexOf('title="/repos/owner/repo"'));
    expect(projectCell).toContain("max-w-0");
    expect(projectCell).toContain("truncate");
    expect(html).toContain("w-full"); // the <th> asking for the remainder
    expect(html).toContain("w-px"); // the numeric columns asking for their own
  });

  it("keeps the full value reachable on the cells it does clip", () => {
    expect(html).toContain('title="/repos/owner/repo"');
  });
});
