import { describe, it, expect } from "vitest";
import { applyDashboardEvent, snapshotHasWorkspace } from "@/lib/grove/activity-stream";
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

  it("session_activity with an older observed_at is dropped (stale delta)", () => {
    const state = snapshot(workspace("a", "working", "2026-06-01T00:00:10Z"));
    const event: DashboardEvent = {
      kind: "session_activity",
      seq: 5,
      workspace: workspace("a", "idle", "2026-06-01T00:00:05Z"),
    } as DashboardEvent;
    expect(applyDashboardEvent(state, event)).toBe(state);
  });

  it("session_activity with a newer observed_at is applied", () => {
    const state = snapshot(workspace("a", "working", "2026-06-01T00:00:05Z"));
    const event: DashboardEvent = {
      kind: "session_activity",
      seq: 6,
      workspace: workspace("a", "idle", "2026-06-01T00:00:10Z"),
    } as DashboardEvent;
    const next = applyDashboardEvent(state, event)!;
    const a = next.projects[0].workspaces.find((w) => w.state.id === "a")!;
    expect(a.sessions[0].activity.state).toBe("idle");
  });

  it("session_activity with a missing observed_at fails open (patch applied)", () => {
    const state = snapshot(workspace("a", "working", "2026-06-01T00:00:10Z"));
    const incoming = workspace("a", "idle");
    // Simulate a daemon that omitted the timestamp — never freeze the card.
    (incoming as { observed_at?: string }).observed_at = undefined;
    const event: DashboardEvent = {
      kind: "session_activity",
      seq: 7,
      workspace: incoming,
    } as DashboardEvent;
    const next = applyDashboardEvent(state, event)!;
    const a = next.projects[0].workspaces.find((w) => w.state.id === "a")!;
    expect(a.sessions[0].activity.state).toBe("idle");
  });

  it("workspace_changed / heartbeat leave state unchanged", () => {
    const state = snapshot(workspace("a", "working"));
    for (const kind of ["workspace_changed", "heartbeat"] as const) {
      const event = { kind, seq: 4 } as DashboardEvent;
      expect(applyDashboardEvent(state, event)).toBe(state);
    }
  });
});

describe("snapshotHasWorkspace", () => {
  it("is true for a workspace present in the snapshot", () => {
    const state = snapshot(workspace("a", "starting"), workspace("b", "working"));
    expect(snapshotHasWorkspace(state, "a")).toBe(true);
    expect(snapshotHasWorkspace(state, "b")).toBe(true);
  });

  it("is false for a workspace not yet in the snapshot (out-of-band create, #49)", () => {
    // The hook turns this `false` into a full snapshot re-fetch so a workspace
    // created by a separate process — whose poll-driven `session_activity`
    // delta the reducer drops — still appears without a reconnect.
    const state = snapshot(workspace("a", "starting"));
    expect(snapshotHasWorkspace(state, "zzz")).toBe(false);
  });

  it("is false for a null snapshot (pre-connect)", () => {
    expect(snapshotHasWorkspace(null, "a")).toBe(false);
  });
});
