import { describe, it, expect } from "vitest";
import { WorkspaceCardModel } from "@/lib/grove/workspace-card";
import { workspace } from "@/tests/_helpers/activity-fixtures";
import type { WorkspaceStateView } from "@/lib/grove/types";

function ws(over: Partial<WorkspaceStateView> = {}): WorkspaceStateView {
  return {
    id: "w1",
    title: "test",
    repo_root: "/repos/Grove",
    branch: "feat/x",
    base_branch: "main",
    worktree_path: "/x",
    tmux_session: "grove-x",
    agent_name: "claude",
    status: "active",
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-01T00:00:00Z",
    paused_at: null,
    error_detail: null,
    description: null,
    init_status: null,
    init_duration_ms: null,
    branch_provenance: "grove",
    placement: "worktree",
    ...over,
  } as WorkspaceStateView;
}

describe("WorkspaceCardModel", () => {
  it("fromState constructs without stats", () => {
    const m = WorkspaceCardModel.fromState(ws());
    expect(m.state.id).toBe("w1");
    expect(m.stats).toBeNull();
  });

  it("fromActivity derives the stat trio (incl. dirty_files) from the activity view", () => {
    const a = {
      ...workspace("w1", "working"),
      base_ahead: 3,
      base_behind: 1,
      dirty_files: 2,
    };
    const m = WorkspaceCardModel.fromActivity(a);
    expect(m.state.id).toBe("w1");
    expect(m.stats).toEqual({ ahead: 3, behind: 1, dirty: 2 });
  });

  it("fromActivity with all-zero stats still yields stats (not the placeholder)", () => {
    const m = WorkspaceCardModel.fromActivity(workspace("w1", "idle"));
    expect(m.stats).toEqual({ ahead: 0, behind: 0, dirty: 0 });
  });

  it("displayStatus is the daemon's promoted status", () => {
    const m = WorkspaceCardModel.fromState(ws({ status: "idle" }));
    expect(m.displayStatus).toBe("idle");
  });

  it.each([
    ["active", true],
    ["idle", true],
    ["paused", false],
    ["offline", false],
    ["orphaned", false],
    ["error", false],
  ])("isLive(%s) === %s", (s, expected) => {
    expect(WorkspaceCardModel.fromState(ws({ status: s as never })).isLive).toBe(expected);
  });

  it.each([
    ["orphaned", true],
    ["error", true],
    ["active", false],
  ])("hasAttention(%s) === %s", (s, expected) => {
    expect(WorkspaceCardModel.fromState(ws({ status: s as never })).hasAttention).toBe(expected);
  });

  it("summaryLine before stats is em dash", () => {
    const m = WorkspaceCardModel.fromState(ws());
    expect(m.summaryLine).toBe("—");
  });

  it("summaryLine formats activity counts", () => {
    const m = WorkspaceCardModel.fromActivity({
      ...workspace("w1", "working"),
      base_ahead: 3,
      base_behind: 1,
      dirty_files: 2,
    });
    expect(m.summaryLine).toBe("ahead 3 · behind 1 · 2 dirty");
  });

  it("summaryLine all zero", () => {
    const m = WorkspaceCardModel.fromActivity(workspace("w1", "idle"));
    expect(m.summaryLine).toBe("ahead 0 · behind 0 · 0 dirty");
  });
});
