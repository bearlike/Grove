import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { pinsFirst } from "@/components/grove/fleet/sidebar-pins";
import { fleetGroups, fleetRows } from "@/components/grove/fleet/filter";
import { project, snapshot, workspace } from "@/tests/fixtures/fleet";

const rows = fleetRows(snapshot([
  project("Alpha", "/repos/alpha", [workspace({ id: "recent" }), workspace({ id: "first-pin" }), workspace({ id: "last-pin" })]),
  project("Beta", "/repos/beta", [workspace({ id: "beta" })]),
]));
const ids = (items: typeof rows): string[] => items.map((row) => row.workspace.state.id);

describe("sidebar pins", () => {
  it("puts the last pin first while preserving unpinned recency and the input", () => {
    expect(ids(pinsFirst(rows, ["last-pin", "first-pin"]))).toEqual(["last-pin", "first-pin", "recent", "beta"]);
    expect(ids(rows)).toEqual(["recent", "first-pin", "last-pin", "beta"]);
  });

  it("ignores pins outside the visible rows and keeps filtering intact", () => {
    expect(ids(pinsFirst(rows.slice(0, 1), ["missing", "last-pin"]))).toEqual(["recent"]);
    expect(ids(pinsFirst(rows, []))).toEqual(ids(rows));
  });

  it("pins within each group without moving a session to another project", () => {
    const groups = fleetGroups(rows, "project").map((group) => ({
      ...group,
      rows: pinsFirst(group.rows, ["beta", "last-pin", "first-pin"]),
    }));
    expect(groups.map((group) => [group.repoName, ids(group.rows)])).toEqual([
      ["Alpha", ["last-pin", "first-pin", "recent"]],
      ["Beta", ["beta"]],
    ]);
  });

  it("adds icon-led new-tab and pin actions to the existing right-click menu", () => {
    const tree = readFileSync("components/grove/fleet/fleet-tree.tsx", "utf8");
    expect(tree).toContain("onContextMenu=");
    expect(tree).toContain('pinned ? " Pinned session." : ""');
    const menu = tree.slice(tree.indexOf('<DropdownMenuContent align="end">'), tree.indexOf("</DropdownMenuContent>"));
    expect(menu).toContain('target="_blank"');
    expect(menu).toContain('rel="noopener noreferrer"');
    expect(menu).toContain("Open in new tab");
    expect(menu).toContain('pinned ? "Unpin session" : "Pin session"');
    for (const icon of ["ExternalLinkIcon", "PinIcon", "PinOffIcon", "PencilIcon", "FileTextIcon", "Trash2Icon"]) {
      expect(menu).toContain(`<${icon}`);
    }
  });
});
