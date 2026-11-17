import { describe, expect, it } from "vitest";

import { NO_FILTER, filterRows } from "@/components/grove/fleet/filter";
import type { FleetRow } from "@/components/grove/fleet/types";

import { workspace } from "../fixtures/fleet";

function row(id: string, createdBy: string | null): FleetRow {
  const built = workspace({ id });
  return {
    workspace: { ...built, state: { ...built.state, created_by: createdBy } },
    repoName: "grove",
    repoRoot: "/repos/grove",
  };
}

const ROWS = [row("alice-1", "alice"), row("bob-1", "bob"), row("legacy", null)];

describe("fleet creator filter (#965)", () => {
  it("opens on the caller's own workspaces", () => {
    expect(filterRows(ROWS, NO_FILTER, "alice").map((r) => r.workspace.state.id)).toEqual([
      "alice-1",
    ]);
  });

  it("reads a workspace with no recorded creator as the host's own user", () => {
    expect(filterRows(ROWS, NO_FILTER, "local").map((r) => r.workspace.state.id)).toEqual([
      "legacy",
    ]);
  });

  it("shows everyone's when the toggle is on", () => {
    expect(filterRows(ROWS, { ...NO_FILTER, everyone: true }, "alice")).toHaveLength(3);
  });

  it("narrows nothing until the caller is known, so the fleet never flashes empty", () => {
    expect(filterRows(ROWS, NO_FILTER, undefined)).toHaveLength(3);
  });
});
