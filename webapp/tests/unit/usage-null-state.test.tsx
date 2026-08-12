import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AbbreviatedNumber } from "@/components/grove/usage/abbreviated-number";
import { UsageActivity } from "@/components/grove/usage/activity";
import { UsageCost } from "@/components/grove/usage/cost";
import { UsageSummary } from "@/components/grove/usage/summary";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { UsageActivityView, UsageSummaryView } from "@/lib/grove/api";

/**
 * An absence is never the loudest thing on the screen.
 *
 * The page's other tests pin what it renders; these pin how quiet it is when it
 * has nothing to render, which is the half that regressed. `not measured` was
 * shipping at `text-2xl font-semibold` — the size and weight of the largest real
 * figure on the page — so the boldest text a user saw was the absence of data.
 *
 * Classes are asserted deliberately: the treatment IS a set of class names and
 * there is no other artifact of it.
 *
 * Scoped to the reachable cases. Every field in the headline tile row is a plain
 * `number` on the wire, so an unmeasured TILE cannot happen and is guarded by
 * the prop type instead of by a branch. What genuinely can be null is a token
 * total (breakdown, sessions), a quota's `used`/`limit`, and cost — so those are
 * what these cover.
 */

const COVERAGE = {
  sources: [],
  degraded_source_count: 0,
  cost_available: false,
  quota_available: true,
};

/** This host's real shape: counts measured, no price table. */
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
  coverage: COVERAGE,
};

const ACTIVITY: UsageActivityView = {
  metric: "tokens",
  tz: "UTC",
  total: 35_900_000_000,
  max_value: 379_601_675,
  buckets: [{ day: "2026-08-01", value: 379_601_675, sessions: 3 }],
  coverage: COVERAGE,
};

const render = (node: React.ReactNode): string =>
  renderToStaticMarkup(<TooltipProvider>{node}</TooltipProvider>);

/** The tag that actually carries `not measured`, with its classes. */
function absenceTag(html: string): string {
  const match = html.match(/<[a-z]+[^>]*>(?=[^<]*not measured)/g);
  if (!match) throw new Error("no 'not measured' in markup");
  return match[match.length - 1]!;
}

describe("the null state is quieter than the data it replaces", () => {
  it("Cost states its absence without the display figure's size or weight", () => {
    const tag = absenceTag(render(<UsageCost summary={SUMMARY} failed={false} />));

    expect(tag).not.toContain("font-semibold");
    expect(tag).not.toContain("text-2xl");
    expect(tag).toContain("text-lg");
    expect(tag).toContain("text-content-tertiary");
  });

  it("a null figure refuses the weight of the container it lands in", () => {
    // The second route by which the absence shouted, and the subtler one:
    // `AbbreviatedNumber` renders inside whatever the caller styled, and its
    // callers run from a display figure down to an inline table cell. Weight
    // and tier are safe to force here because an absence is never bold and
    // never primary at any size; the SIZE stays the caller's.
    const html = renderToStaticMarkup(
      <p className="text-2xl font-semibold">
        <AbbreviatedNumber value={null} />
      </p>,
    );

    expect(absenceTag(html)).toContain("font-normal");
    expect(absenceTag(html)).toContain("text-content-tertiary");
  });

  it("keeps weight and the primary tier for a figure that IS measured", () => {
    // The control. Without it every assertion above would pass on a page that
    // had simply stopped emphasising anything at all.
    const html = render(<UsageSummary summary={SUMMARY} failed={false} />);

    expect(html).toContain("text-2xl font-semibold text-content-primary");
  });

  it("a token total is a quantity, not an outcome, so it is never coloured", () => {
    const html = render(<UsageActivity activity={ACTIVITY} failed={false} />);

    expect(html).not.toContain("text-success");
    expect(html).toContain("text-content-primary");
  });
});
