import { describe, it, expect } from "vitest";
import { applyDashboardEvent } from "@/lib/grove/activity-stream";
import { snapshot, workspace } from "@/tests/_helpers/activity-fixtures";
import type { DashboardEvent } from "@/lib/grove/types";

describe("applyDashboardEvent", () => {
  it("snapshot event replaces state wholesale", () => {
    const snap = snapshot(workspace("a", "starting"));
    const event: DashboardEvent = { kind: "snapshot", seq: 1, snapshot: snap } as DashboardEvent;
    expect(applyDashboardEvent(null, event)).toBe(snap);
  });

  it("session_activity patches the one workspace and recomputes counts", () => {
    const state = snapshot(workspace("a", "starting"), workspace("b", "starting"));
    const event: DashboardEvent = {
      kind: "session_activity",
      seq: 2,
      workspace: workspace("a", "waiting"),
    } as DashboardEvent;
    const next = applyDashboardEvent(state, event)!;
    const a = next.projects[0].workspaces.find((w) => w.state.id === "a")!;
    expect(a.sessions[0].activity.state).toBe("waiting");
    expect(next.needs_attention).toBe(1); // "a" now wants attention
    expect(next.total_workspaces).toBe(2);
    // "b" is untouched (stable wall).
    expect(next.projects[0].workspaces.find((w) => w.state.id === "b")).toBe(
      state.projects[0].workspaces[1],
    );
  });

  it("session_activity for an unknown workspace is a no-op", () => {
    const state = snapshot(workspace("a", "starting"));
    const event: DashboardEvent = {
      kind: "session_activity",
      seq: 3,
      workspace: workspace("zzz", "working"),
    } as DashboardEvent;
    expect(applyDashboardEvent(state, event)).toBe(state);
  });

  it("workspace_changed / heartbeat leave state unchanged", () => {
    const state = snapshot(workspace("a", "working"));
    for (const kind of ["workspace_changed", "heartbeat"] as const) {
      const event = { kind, seq: 4 } as DashboardEvent;
      expect(applyDashboardEvent(state, event)).toBe(state);
    }
  });
});
