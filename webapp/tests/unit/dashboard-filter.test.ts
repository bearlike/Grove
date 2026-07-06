import { describe, it, expect } from "vitest";
import {
  computeFacets,
  displayState,
  compareByAttention,
  selectSections,
  type GridView,
} from "@/lib/grove/dashboard-filter";
import { snapshot, workspace } from "@/tests/_helpers/activity-fixtures";
import type { DashboardSnapshotView, WorkspaceActivityView } from "@/lib/grove/types";

// The post-#96 presentation policy: `computeFacets` (FROZEN — the sidebar reads
// it) plus the one grid selector `selectSections`, which turns the store's view
// intent into repo-grouped, attention-first sections. The old Set-based filter +
// lens + flatten machinery is gone (intent lives in the Zustand ui-store now).

/** Default (no-op) view intent — the "show everything" baseline. */
function view(over: Partial<GridView> = {}): GridView {
  return { scopeRepo: null, hiddenStates: [], attentionOnly: false, query: "", ...over };
}

// Two projects so repo scoping/grouping is exercised; r2's card is sessionless.
function multi(): DashboardSnapshotView {
  const noSession = { ...workspace("c", "idle"), sessions: [] as never[] };
  return {
    projects: [
      { repo_root: "/r1", repo_name: "r1", cwd: "/r1", workspaces: [workspace("a", "working"), workspace("b", "waiting")] },
      { repo_root: "/r2", repo_name: "r2", cwd: "/r2", workspaces: [noSession] },
    ],
    generated_at: "2026-06-01T00:00:00Z",
    total_workspaces: 3,
    needs_attention: 1,
  };
}

describe("displayState", () => {
  it("falls back to unknown when there is no session", () => {
    expect(displayState(workspace("a", "working"))).toBe("working");
    expect(displayState({ ...workspace("c", "idle"), sessions: [] as never[] })).toBe("unknown");
  });
});

describe("computeFacets (FROZEN — sidebar input)", () => {
  it("reports project + state distribution and counts", () => {
    const f = computeFacets(multi());
    expect(f.total).toBe(3);
    expect(f.attention).toBe(1); // the waiting one
    expect(f.projects.map((p) => p.repo_name)).toEqual(["r1", "r2"]);
    expect(f.projects.map((p) => p.count)).toEqual([2, 1]);
    const states = Object.fromEntries(f.states.map((s) => [s.state, s.count]));
    expect(states).toMatchObject({ working: 1, waiting: 1, unknown: 1 });
  });

  it("collapses nested-project groups (shared repo_root, different cwd) to one facet entry (#151)", () => {
    // The engine emits one ProjectGroup per (repo_root, cwd) for nested projects
    // (#101) — two groups here share "/r1" but scope different subpaths.
    const nested: DashboardSnapshotView = {
      projects: [
        { repo_root: "/r1", repo_name: "r1", cwd: "/r1", workspaces: [workspace("a", "working")] },
        {
          repo_root: "/r1",
          repo_name: "r1",
          cwd: "/r1/services/api",
          workspaces: [workspace("b", "waiting")],
        },
      ],
      generated_at: "2026-06-01T00:00:00Z",
      total_workspaces: 2,
      needs_attention: 1,
    };
    const f = computeFacets(nested);
    expect(f.projects).toEqual([{ repo_root: "/r1", repo_name: "r1", count: 2 }]);
  });
});

describe("selectSections (the grid selector)", () => {
  it("groups by repo in snapshot order, attention-first within a section", () => {
    const sections = selectSections(multi(), view());
    expect(sections.map((s) => s.repo_root)).toEqual(["/r1", "/r2"]);
    // r1: waiting (attention) outranks working.
    expect(sections[0].workspaces.map((w) => w.state.id)).toEqual(["b", "a"]);
  });

  it("scopeRepo narrows to one section", () => {
    const sections = selectSections(multi(), view({ scopeRepo: "/r2" }));
    expect(sections.map((s) => s.repo_root)).toEqual(["/r2"]);
  });

  it("hiddenStates drops matching cards and empty sections", () => {
    // Hide working + waiting → r1 empties out; only the unknown (r2) survives.
    const sections = selectSections(multi(), view({ hiddenStates: ["working", "waiting"] }));
    expect(sections.map((s) => s.repo_name)).toEqual(["r2"]);
  });

  it("attentionOnly keeps only the cards that want the human", () => {
    const sections = selectSections(multi(), view({ attentionOnly: true }));
    const all = sections.flatMap((s) => s.workspaces);
    expect(all).toHaveLength(1);
    expect(displayState(all[0])).toBe("waiting");
  });

  it("query matches title (case-insensitive) and branch", () => {
    const byTitle = selectSections(multi(), view({ query: "T-A" }));
    expect(byTitle.flatMap((s) => s.workspaces).map((w) => w.state.id)).toEqual(["a"]);
    // fixture branch is "b" on every workspace → matches all three.
    expect(
      selectSections(multi(), view({ query: "b" })).flatMap((s) => s.workspaces),
    ).toHaveLength(3);
  });

  it("returns no sections when nothing matches", () => {
    expect(selectSections(multi(), view({ query: "zzz" }))).toEqual([]);
  });
});

describe("compareByAttention", () => {
  it("ranks every action-required state above working", () => {
    for (const state of ["waiting", "blocked", "error"] as const) {
      expect(compareByAttention(workspace("a", state), workspace("b", "working"))).toBeLessThan(0);
      expect(compareByAttention(workspace("b", "working"), workspace("a", state))).toBeGreaterThan(0);
    }
  });

  it("ranks working above dormant", () => {
    expect(compareByAttention(workspace("w", "working"), workspace("i", "idle"))).toBeLessThan(0);
  });

  it("orders attention before working before dormant within a repo", () => {
    const ws: WorkspaceActivityView[] = [
      workspace("idle", "idle"),
      workspace("work", "working"),
      workspace("block", "blocked"),
    ];
    const sorted = selectSections(snapshot(...ws), view())[0].workspaces.map((w) => w.state.id);
    expect(sorted).toEqual(["block", "work", "idle"]);
  });

  it("a sessionless tmux-active workspace ranks as working, never attention", () => {
    const noSession = { ...workspace("tmux", "idle"), sessions: [] as never[] };
    // fixture status is "active" → fallback puts it in the active tier.
    const sorted = selectSections(snapshot(noSession, workspace("wait", "waiting")), view())[0]
      .workspaces.map((w) => w.state.id);
    expect(sorted).toEqual(["wait", "tmux"]);
  });

  it("tie-breaks equal-rank workspaces by observed_at desc, then stable", () => {
    const older = workspace("old", "idle", "2026-06-01T00:00:00Z");
    const newer = workspace("new", "idle", "2026-06-02T00:00:00Z");
    const sorted = selectSections(snapshot(older, newer), view())[0].workspaces.map((w) => w.state.id);
    expect(sorted).toEqual(["new", "old"]);
  });

  it("is stable for fully-equal keys (preserves incoming order)", () => {
    const a = workspace("a", "idle", "2026-06-01T00:00:00Z");
    const b = workspace("b", "idle", "2026-06-01T00:00:00Z");
    const sorted = selectSections(snapshot(a, b), view())[0].workspaces.map((w) => w.state.id);
    expect(sorted).toEqual(["a", "b"]);
  });
});
