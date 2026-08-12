import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { UsageQuota } from "@/components/grove/usage/quota";
import { SectionFailure, UsageSection } from "@/components/grove/usage/section";
import { UsageSummary } from "@/components/grove/usage/summary";
import type { UsageQuotasView } from "@/lib/grove/api";

/**
 * What each usage panel renders when it has nothing to render.
 *
 * The failures pinned here are the ones a happy-path test cannot see. Two
 * sections used to answer a failed query with a bare paragraph where six cards
 * had been, so the page below them jumped the moment the query resolved — and
 * that paragraph wore the identical muted treatment as "not measured", which
 * made a dropped request indistinguishable from a store that genuinely holds no
 * data. A reader draws a conclusion about their own spend from that difference.
 *
 * These assert the CONTRACT — a card, an alert role, a retry control, and a word
 * carrying the state beside the colour — never the class list that produces it.
 */
function render(node: React.ReactNode): string {
  return renderToStaticMarkup(<>{node}</>);
}

const NO_ACCOUNTS: UsageQuotasView = {
  accounts: [],
  coverage: { sources: [], degraded_source_count: 0, cost_available: false, quota_available: true },
};

describe("a section that failed", () => {
  it("says what failed in words, so the colour is never the only carrier", () => {
    const html = render(<SectionFailure detail="Quota could not be read." />);
    expect(html).toContain("Quota could not be read.");
    expect(html).toContain('role="alert"');
  });

  it("offers the one action that might fix it", () => {
    const html = render(<SectionFailure detail="Nope." onRetry={() => undefined} />);
    expect(html).toContain("Try again");
  });

  it("says it is retrying rather than looking inert", () => {
    const html = render(<SectionFailure detail="Nope." onRetry={() => undefined} retrying />);
    expect(html).toContain("Retrying");
    expect(html).toContain("disabled");
  });

  // A retry button that cannot retry is worse than no button: it invites a
  // click, does nothing, and teaches the reader the surface is broken.
  it("offers no action where the caller has none to give", () => {
    expect(render(<SectionFailure detail="Nope." />)).not.toContain("Try again");
  });
});

describe("UsageSection", () => {
  it("shows the failure instead of the body, and never both", () => {
    const html = render(
      <UsageSection title="By model" failed failure="The model breakdown could not be loaded.">
        <p>rows nobody should see</p>
      </UsageSection>,
    );
    expect(html).toContain("The model breakdown could not be loaded.");
    expect(html).not.toContain("rows nobody should see");
  });

  it("prefers the failure over the skeleton when a query both failed and is unresolved", () => {
    const html = render(
      <UsageSection title="By model" failed loading failure="Broke.">
        <p>body</p>
      </UsageSection>,
    );
    expect(html).toContain("Broke.");
    expect(html).toContain('role="alert"');
  });
});

/**
 * The headline row and the quota row are grids of tiles rather than one card, so
 * they cannot route a failure through `UsageSection`. They must still answer
 * inside a card on the same grid, or the row collapses from ~96px to one line of
 * text and everything below it moves.
 */
describe("the tile grids answer inside a card", () => {
  it("keeps the totals row a card when the summary query fails", () => {
    const html = render(<UsageSummary summary={undefined} failed onRetry={() => undefined} />);
    expect(html).toContain('data-testid="usage-totals-failed"');
    expect(html).toContain('data-slot="card"');
    expect(html).toContain("Usage totals could not be loaded.");
    expect(html).toContain("Try again");
  });

  it("keeps the quota row a card when the probe fails", () => {
    const html = render(<UsageQuota quotas={undefined} failed onRetry={() => undefined} />);
    expect(html).toContain('data-testid="usage-quota-failed"');
    expect(html).toContain('data-slot="card"');
    expect(html).toContain("Quota could not be read");
  });

  // The distinction this whole file exists for: an unconfigured profile is a
  // fact about the host with nothing to retry, and it must not wear the alert
  // treatment a failed probe wears.
  it("distinguishes an unconfigured profile from a failed probe", () => {
    const html = render(<UsageQuota quotas={NO_ACCOUNTS} failed={false} />);
    expect(html).toContain('data-testid="usage-quota-empty"');
    expect(html).toContain("No subscription profile is configured");
    expect(html).not.toContain('role="alert"');
    expect(html).not.toContain("Try again");
  });
});
