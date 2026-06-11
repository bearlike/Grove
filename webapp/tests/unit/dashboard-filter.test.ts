import { describe, it, expect, beforeEach } from "vitest";
import {
  activeFilterCount,
  computeFacets,
  displayState,
  emptyFilter,
  filterSnapshot,
  groupsForLens,
  passesLens,
  sortSnapshotByActivity,
} from "@/lib/grove/dashboard-filter";
import { loadFilter, saveFilter } from "@/lib/grove/filter-persistence";
import { snapshot, workspace } from "@/tests/_helpers/activity-fixtures";
import type {
  AgentActivityState,
  DashboardSnapshotView,
  ProjectGroupView,
  WorkspaceActivityView,
} from "@/lib/grove/types";

describe("lens filtering", () => {
  it("passesLens matches the TUI semantics", () => {
    expect(passesLens(workspace("a", "starting"), "all")).toBe(true);
    expect(passesLens(workspace("a", "starting"), "attention")).toBe(false);
    expect(passesLens(workspace("a", "waiting"), "attention")).toBe(true);
    expect(passesLens(workspace("a", "starting"), "active")).toBe(true);
    expect(passesLens(workspace("a", "idle"), "active")).toBe(false);
  });

  it("groupsForLens drops groups emptied by the lens", () => {
    const state = snapshot(workspace("a", "idle"), workspace("b", "idle"));
    expect(groupsForLens(state, "all")[0].workspaces).toHaveLength(2);
    expect(groupsForLens(state, "attention")).toHaveLength(0);
  });
});

describe("consolidated filter", () => {
  // Two projects so project-hiding is exercised.
  function multi(): DashboardSnapshotView {
    const noSession = { ...workspace("c", "idle"), sessions: [] as never[] };
    return {
      projects: [
        { repo_root: "/r1", repo_name: "r1", workspaces: [workspace("a", "working"), workspace("b", "waiting")] },
        { repo_root: "/r2", repo_name: "r2", workspaces: [noSession] },
      ],
      generated_at: "2026-06-01T00:00:00Z",
      total_workspaces: 3,
      needs_attention: 1,
    };
  }

  it("displayState falls back to unknown when there is no session", () => {
    expect(displayState(workspace("a", "working"))).toBe("working");
    expect(displayState({ ...workspace("c", "idle"), sessions: [] as never[] })).toBe("unknown");
  });

  it("computeFacets reports project + state distribution and counts", () => {
    const f = computeFacets(multi());
    expect(f.total).toBe(3);
    expect(f.attention).toBe(1); // the waiting one
    expect(f.projects.map((p) => p.repo_name)).toEqual(["r1", "r2"]);
    const states = Object.fromEntries(f.states.map((s) => [s.state, s.count]));
    expect(states).toMatchObject({ working: 1, waiting: 1, unknown: 1 });
  });

  it("filterSnapshot hides a project", () => {
    const groups = filterSnapshot(multi(), { ...emptyFilter(), hiddenProjects: new Set(["/r2"]) });
    expect(groups.map((g) => g.repo_name)).toEqual(["r1"]);
  });

  it("filterSnapshot hides a state and drops emptied groups", () => {
    // Hide working + waiting → r1 empties out; only the unknown (r2) survives.
    const groups = filterSnapshot(multi(), {
      ...emptyFilter(),
      hiddenStates: new Set(["working", "waiting"]),
    });
    expect(groups).toHaveLength(1);
    expect(groups[0].repo_name).toBe("r2");
  });

  it("filterSnapshot attentionOnly keeps only the sessions that want the human", () => {
    const groups = filterSnapshot(multi(), { ...emptyFilter(), attentionOnly: true });
    const all = groups.flatMap((g) => g.workspaces);
    expect(all).toHaveLength(1);
    expect(displayState(all[0])).toBe("waiting");
  });

  it("activeFilterCount sums every active constraint", () => {
    expect(activeFilterCount(emptyFilter())).toBe(0);
    expect(
      activeFilterCount({
        hiddenProjects: new Set(["/r2"]),
        hiddenStates: new Set(["idle", "unknown"]),
        attentionOnly: true,
      }),
    ).toBe(4);
  });
});

describe("sortSnapshotByActivity", () => {
  const group = (repo: string, ...ws: WorkspaceActivityView[]): ProjectGroupView => ({
    repo_root: repo,
    repo_name: repo.replace("/", ""),
    workspaces: ws,
  });

  it("floats working above attention above idle within a project", () => {
    // Provide them out of rank order; the sort must reorder by activityRank.
    const g = group("/r", workspace("idle", "idle"), workspace("wait", "waiting"), workspace("work", "working"));
    const sorted = sortSnapshotByActivity([g]);
    expect(sorted[0].workspaces.map((w) => w.state.id)).toEqual(["work", "wait", "idle"]);
  });

  it("orders a project with an active session before an all-idle project", () => {
    const idleProject = group("/r1", workspace("i1", "idle"), workspace("i2", "idle"));
    const activeProject = group("/r2", workspace("i3", "idle"), workspace("w", "working"));
    // Active project given second — must float to the front by its min rank.
    const sorted = sortSnapshotByActivity([idleProject, activeProject]);
    expect(sorted.map((g) => g.repo_root)).toEqual(["/r2", "/r1"]);
  });

  it("tie-breaks equal-rank workspaces by observed_at desc, then stable", () => {
    const older = workspace("old", "idle", "2026-06-01T00:00:00Z");
    const newer = workspace("new", "idle", "2026-06-02T00:00:00Z");
    const sorted = sortSnapshotByActivity([group("/r", older, newer)]);
    // freshest observation first within the same (dormant) tier
    expect(sorted[0].workspaces.map((w) => w.state.id)).toEqual(["new", "old"]);
  });

  it("is stable for fully-equal keys (preserves incoming order)", () => {
    const a = workspace("a", "idle", "2026-06-01T00:00:00Z");
    const b = workspace("b", "idle", "2026-06-01T00:00:00Z");
    const sorted = sortSnapshotByActivity([group("/r", a, b)]);
    expect(sorted[0].workspaces.map((w) => w.state.id)).toEqual(["a", "b"]);
  });
});

describe("filter persistence round-trip", () => {
  beforeEach(() => window.localStorage.clear());

  it("save → load yields equal Sets", () => {
    const filter = {
      hiddenProjects: new Set(["/r1", "/r2"]),
      hiddenStates: new Set<AgentActivityState>(["idle", "unknown"]),
      attentionOnly: true,
    };
    saveFilter(filter);
    const loaded = loadFilter();
    expect([...loaded.hiddenProjects].sort()).toEqual(["/r1", "/r2"]);
    expect([...loaded.hiddenStates].sort()).toEqual(["idle", "unknown"]);
    expect(loaded.attentionOnly).toBe(true);
    expect(loaded.hiddenProjects).toBeInstanceOf(Set);
    expect(loaded.hiddenStates).toBeInstanceOf(Set);
  });

  it("missing localStorage → emptyFilter", () => {
    const loaded = loadFilter();
    expect(loaded.hiddenProjects.size).toBe(0);
    expect(loaded.hiddenStates.size).toBe(0);
    expect(loaded.attentionOnly).toBe(false);
  });

  it("corrupt localStorage → emptyFilter", () => {
    window.localStorage.setItem("grove.dashboard.filter", "{not json");
    const loaded = loadFilter();
    expect(loaded.hiddenProjects.size).toBe(0);
    expect(loaded.attentionOnly).toBe(false);
  });
});
