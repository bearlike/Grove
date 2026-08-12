import { describe, expect, it } from "vitest";

import { activityHue } from "@/components/grove/fleet/fleet-tree";
import { agentAccent, agentTone } from "@/components/grove/fleet/tokens";
import type { AgentState } from "@/components/grove/fleet/types";

/**
 * The rail row's trailing glyph used to render `text-muted-foreground` no
 * matter what the agent was doing, so "which of twenty workspaces wants me"
 * had to be answered by reading twenty rows of prose. `activityHue` is the
 * fix, and every case here is really a case against `agentTone`/`agentAccent`
 * — the SAME two tables `AgentStateBadge` reads — because a hand-written
 * second mapping is exactly what would drift from them silently.
 */

const AGENT_STATES: readonly AgentState[] = [
  "starting",
  "working",
  "waiting",
  "blocked",
  "idle",
  "error",
  "unknown",
];

describe("activityHue", () => {
  it("colours the mid-flight states the accent table already marks", () => {
    for (const state of AGENT_STATES) {
      if (agentAccent(state) !== undefined) {
        expect(activityHue(state), `activityHue(${state})`).toBe("text-warning");
      }
    }
    expect(activityHue("starting")).toBe("text-warning");
    expect(activityHue("working")).toBe("text-warning");
  });

  it("colours the states that need a human the tone table already marks `destructive`", () => {
    for (const state of AGENT_STATES) {
      if (agentTone(state) === "destructive") {
        expect(activityHue(state), `activityHue(${state})`).toBe("text-destructive");
      }
    }
    expect(activityHue("waiting")).toBe("text-destructive");
    expect(activityHue("blocked")).toBe("text-destructive");
    expect(activityHue("error")).toBe("text-destructive");
  });

  it("stays neutral for the resting states, per §4.1's 'neutral is the default'", () => {
    expect(activityHue("idle")).toBeUndefined();
    expect(activityHue("unknown")).toBeUndefined();
  });

  it("never doubles up — mid-flight and destructive are disjoint by construction", () => {
    // If a future state ever earned both an accent and a destructive tone,
    // this function would have to pick a winner instead of composing one from
    // the two tables cleanly. Pinning the disjointness is what keeps that a
    // decision someone makes on purpose, not a silent `if`-order accident.
    for (const state of AGENT_STATES) {
      const midFlight = agentAccent(state) !== undefined;
      const needsHuman = agentTone(state) === "destructive";
      expect(midFlight && needsHuman, `${state} claims both`).toBe(false);
    }
  });
});
