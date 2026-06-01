import { describe, it, expect } from "vitest";
import { WorkspaceCardModel } from "@/lib/grove/workspace-card";
import type { WorkspaceStateView, WorkspacePeekView } from "@/lib/grove/types";

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
    ...over,
  } as WorkspaceStateView;
}

function peek(over: Partial<WorkspacePeekView> = {}): WorkspacePeekView {
  return {
    state: ws(),
    base_ahead: 0,
    base_behind: 0,
    diff_added: 0,
    diff_removed: 0,
    dirty_files: 0,
    recent_commits: [],
    agent_snapshot: null,
    snapshot_taken_at: null,
    ...over,
  } as WorkspacePeekView;
}

describe("WorkspaceCardModel", () => {
  it("fromState constructs without peek", () => {
    const m = WorkspaceCardModel.fromState(ws());
    expect(m.state.id).toBe("w1");
    expect(m.peek).toBeNull();
  });

  it("withPeek returns a NEW instance (immutability)", () => {
    const m = WorkspaceCardModel.fromState(ws());
    const m2 = m.withPeek(peek({ base_ahead: 3 }));
    expect(m).not.toBe(m2);
    expect(m.peek).toBeNull();
    expect(m2.peek?.base_ahead).toBe(3);
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

  it("summaryLine before peek is em dash", () => {
    const m = WorkspaceCardModel.fromState(ws());
    expect(m.summaryLine).toBe("—");
  });

  it("summaryLine formats peek counts", () => {
    const m = WorkspaceCardModel.fromState(ws()).withPeek(
      peek({ base_ahead: 3, base_behind: 1, dirty_files: 2 }),
    );
    expect(m.summaryLine).toBe("ahead 3 · behind 1 · 2 dirty");
  });

  it("summaryLine all zero", () => {
    const m = WorkspaceCardModel.fromState(ws()).withPeek(peek());
    expect(m.summaryLine).toBe("ahead 0 · behind 0 · 0 dirty");
  });
});
