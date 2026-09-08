import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { FleetFilterMenu, showsProjectHideControls } from "@/components/grove/fleet/fleet-filter";
import { NO_FILTER } from "@/components/grove/fleet/filter";

describe("FleetFilterMenu", () => {
  it("uses an icon-only sidebar trigger with its active count", () => {
    const html = renderToStaticMarkup(
      FleetFilterMenu({
        variant: "sidebar",
        filter: { ...NO_FILTER, attentionOnly: true, hiddenStates: ["idle"] },
        onFilterChange: () => undefined,
        facets: { states: [], projects: [], attention: 1 },
      }),
    );

    expect(html).toContain('aria-label="Filter workspaces (2 active)"');
    expect(html).toContain('data-testid="fleet-filter-trigger"');
    expect(html).toContain(">2</span>");
    expect(html).not.toContain(">Filters</span>");
    expect(html).toContain('data-slot="tooltip-trigger"');
    expect(html).toContain('min-h-[24px]');
  });

  it("keeps project hide controls on the dashboard only", () => {
    const facets = {
      states: [],
      projects: [
        { repoRoot: "/repos/alpha", repoName: "Alpha", count: 1 },
        { repoRoot: "/repos/beta", repoName: "Beta", count: 1 },
      ],
      attention: 0,
    };
    const sidebar = renderToStaticMarkup(
      FleetFilterMenu({
        variant: "sidebar",
        filter: NO_FILTER,
        onFilterChange: () => undefined,
        facets,
      }),
    );
    const dashboard = renderToStaticMarkup(
      FleetFilterMenu({
        filter: NO_FILTER,
        onFilterChange: () => undefined,
        facets,
      }),
    );

    expect(sidebar).toContain("fleet-filter-trigger");
    expect(dashboard).toContain(">Filters</span>");
    expect(showsProjectHideControls("sidebar", facets)).toBe(false);
    expect(showsProjectHideControls("fleet", facets)).toBe(true);
  });
});
