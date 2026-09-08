import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { FleetFilterMenu } from "@/components/grove/fleet/fleet-filter";
import {
  activeFilterCount,
  filterRows,
  fleetGroups,
  fleetRows,
  NO_FILTER,
  sortedFleetRows,
} from "@/components/grove/fleet/filter";
import { project, snapshot, workspace } from "@/tests/fixtures/fleet";

const ids = (rows: readonly { workspace: { state: { id: string } } }[]): string[] =>
  rows.map((row) => row.workspace.state.id);

describe("fleetGroups", () => {
  it("returns no groups for no surviving rows", () => {
    expect(fleetGroups([], "none")).toEqual([]);
    expect(fleetGroups([], "project")).toEqual([]);
  });

  it("keeps an ungrouped fleet as one recency-ordered group", () => {
    const rows = sortedFleetRows(
      snapshot([
        project("Grove", "/repos/grove", [
          workspace({ id: "older", lastEventAt: "2026-08-01T00:00:00Z" }),
          workspace({ id: "newer", lastEventAt: "2026-08-03T00:00:00Z" }),
        ]),
      ]),
    );

    expect(fleetGroups(rows, "none")).toEqual([{ key: "all", repoName: null, rows }]);
  });

  it("merges nested project rows by root, orders project names, and retains recency within each group", () => {
    const nested = snapshot([
      project("Zebra", "/repos/zebra", [workspace({ id: "zebra", lastEventAt: "2026-08-02T00:00:00Z" })]),
      project("Alpha", "/repos/alpha", [workspace({ id: "alpha-old", lastEventAt: "2026-08-01T00:00:00Z" })]),
      project("Alpha", "/repos/alpha", [workspace({ id: "alpha-new", lastEventAt: "2026-08-04T00:00:00Z" })]),
    ]);
    const rows = sortedFleetRows(nested);

    expect(
      fleetGroups(rows, "project").map((group) => ({
        key: group.key,
        repoName: group.repoName,
        ids: ids(group.rows),
      })),
    ).toEqual([
      { key: "/repos/alpha", repoName: "Alpha", ids: ["alpha-new", "alpha-old"] },
      { key: "/repos/zebra", repoName: "Zebra", ids: ["zebra"] },
    ]);
  });

  it("keeps same-named projects distinct and breaks their name tie by root", () => {
    const rows = fleetRows(
      snapshot([
        project("Grove", "/repos/second", [workspace({ id: "second" })]),
        project("Grove", "/repos/first", [workspace({ id: "first" })]),
      ]),
    );

    expect(fleetGroups(rows, "project").map(({ key, repoName }) => ({ key, repoName }))).toEqual([
      { key: "/repos/first", repoName: "Grove" },
      { key: "/repos/second", repoName: "Grove" },
    ]);
  });

  it("does not create a heading for a project whose rows the filter removed", () => {
    const rows = fleetRows(
      snapshot([
        project("Grove", "/repos/grove", [workspace({ id: "grove" })]),
        project("Docs", "/repos/docs", [workspace({ id: "docs" })]),
      ]),
    );
    const visible = filterRows(rows, { ...NO_FILTER, hiddenProjects: ["/repos/grove", "/repos/docs"] });

    expect(fleetGroups(visible, "project")).toEqual([]);
  });
});

describe("grouping is an arrangement, not a filter", () => {
  it("does not add to the active filter count", () => {
    expect(activeFilterCount({ ...NO_FILTER, groupBy: "project" })).toBe(0);
  });

  it("marks the filter trigger when grouping is selected", () => {
    const html = renderToStaticMarkup(
      <FleetFilterMenu
        filter={{ ...NO_FILTER, groupBy: "project" }}
        onFilterChange={() => undefined}
        facets={{ states: [], projects: [{ repoRoot: "/repos/grove", repoName: "Grove", count: 1 }], attention: 0 }}
      />,
    );

    expect(html).toContain('data-testid="fleet-filter-trigger"');
    expect(html).toContain('aria-label="Filter workspaces, grouped by project"');
    expect(html).toContain("lucide-list-filter");
    expect(html).toContain(">Filters</span>");
    expect(html).toContain('aria-hidden="true"');
  });
});
