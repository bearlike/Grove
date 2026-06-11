import { describe, it, expect } from "vitest";
import { statusColor } from "@/lib/grove/status-tokens";
import { agentStateColor } from "@/lib/grove/agent-state-tokens";
import { tierForActivity, activityRank } from "@/lib/grove/activity-tier";
import type { AgentActivityState, WorkspaceStatus } from "@/lib/grove/types";

// Streamed SSE deltas are JSON; the TS unions do NOT constrain them at runtime.
// A value the client's enum predates (a daemon shipped a new state) must
// DEGRADE, never crash — the `MAP[bad].field → undefined.field` white-screen is
// exactly the class of bug we're defending against.
const BOGUS_STATUS = "supernova" as WorkspaceStatus;
const BOGUS_STATE = "telepathy" as AgentActivityState;

describe("render hardening: out-of-contract enum values never yield undefined", () => {
  it("statusColor falls back to a defined hex (both themes)", () => {
    expect(statusColor(BOGUS_STATUS, true)).toBeTruthy();
    expect(statusColor(BOGUS_STATUS, false)).toBeTruthy();
  });

  it("agentStateColor falls back to a defined hex (both themes)", () => {
    expect(agentStateColor(BOGUS_STATE, true)).toBeTruthy();
    expect(agentStateColor(BOGUS_STATE, false)).toBeTruthy();
  });

  it("tierForActivity returns a complete dormant treatment, never undefined", () => {
    const t = tierForActivity(BOGUS_STATE, "active");
    expect(t).toBeDefined();
    expect(t.tier).toBe("dormant");
    expect(t.opacityClass).toBeTruthy();
    expect(t.accentVar).toBeTruthy();
  });

  it("activityRank tolerates an unknown state (sorts it as dormant)", () => {
    expect(activityRank(BOGUS_STATE, "idle")).toBe(2);
  });
});
