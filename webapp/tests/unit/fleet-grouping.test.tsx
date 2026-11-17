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

  it("merges nested project rows by root, leads with the most recently active project, and retains recency within each group", () => {
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
      // Alpha's newest row (08-04) moved after Zebra's (08-02), so Alpha leads.
      // Alphabetical order would pass here too, which is why the next test exists.
      { key: "/repos/alpha", repoName: "Alpha", ids: ["alpha-new", "alpha-old"] },
      { key: "/repos/zebra", repoName: "Zebra", ids: ["zebra"] },
    ]);
  });

  it("puts the project whose workspace moved most recently first, whatever its name (#919)", () => {
    const rows = sortedFleetRows(
      snapshot([
        project("Agents", "/repos/agents", [workspace({ id: "stale", lastEventAt: "2026-08-01T00:00:00Z" })]),
        project("Grove", "/repos/grove", [workspace({ id: "fresh", lastEventAt: "2026-08-09T00:00:00Z" })]),
      ]),
    );

    expect(fleetGroups(rows, "project").map(({ repoName, rows: group }) => [repoName, ids(group)])).toEqual([
      ["Grove", ["fresh"]],
      ["Agents", ["stale"]],
    ]);
  });

  it("keeps same-named projects distinct, ordered by their rows", () => {
    const rows = sortedFleetRows(
      snapshot([
        project("Grove", "/repos/second", [workspace({ id: "second", lastEventAt: "2026-08-01T00:00:00Z" })]),
        project("Grove", "/repos/first", [workspace({ id: "first", lastEventAt: "2026-08-02T00:00:00Z" })]),
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
