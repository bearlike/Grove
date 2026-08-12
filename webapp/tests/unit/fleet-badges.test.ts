import { describe, expect, it } from "vitest";

import { agentTone, statusTone } from "@/components/grove/fleet/tokens";
import type { AgentState, WorkspaceStatus } from "@/components/grove/fleet/types";

/**
 * The two badge invariants a fleet card depends on, neither of which any
 * compiler can hold.
 *
 * These are tables over wire unions, so a state the daemon ADDS already fails to
 * compile — that part needs no test. What a type cannot say is how LOUD each
 * entry is allowed to be, and both rules below were violated in shipped code:
 * two axes each claimed the loudest tone for one fact, and the attention signal
 * was duplicated as a fourth badge beside the state it is derived from.
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

const WORKSPACE_STATUSES: readonly WorkspaceStatus[] = [
  "active",
  "running",
  "provisioning",
  "idle",
  "paused",
  "offline",
  "orphaned",
  "error",
];

describe("one `default` per object, across all its axes", () => {
  // The status axis owns the loudest tone because it is the axis a card leads
  // with — identity-plus-state in the header band.
  it("spends `default` on the workspace status axis", () => {
    expect(statusTone("active")).toBe("default");
    expect(statusTone("running")).toBe("default");
  });

  // `working` used to be `default` here, so an active workspace running a
  // working agent rendered two maximally loud marks for one fact.
  it("never spends `default` on the agent axis", () => {
    for (const state of AGENT_STATES) {
      expect(agentTone(state), `agentTone(${state})`).not.toBe("default");
    }
  });
});

/**
 * `needs_attention` is `state ∈ {waiting, blocked, error}` — derived in
 * `core/agents/model.py` as `ATTENTION_STATES`, and NOT a field a client can
 * read independently. The card used to print both it and the state, so
 * "waiting for you" sat beside "needs you".
 *
 * Removing the duplicate mark would have made the fleet's most important signal
 * quieter, so the loudness moved into this table instead. That makes the two
 * spellings of one rule live in two languages with nothing linking them, which
 * is exactly what this test is for.
 */
describe("the agent tone carries `needs you`", () => {
  const ATTENTION_STATES: readonly AgentState[] = ["waiting", "blocked", "error"];

  it("marks every attention state `destructive`", () => {
    for (const state of ATTENTION_STATES) {
      expect(agentTone(state), `agentTone(${state})`).toBe("destructive");
    }
  });

  it("marks nothing else `destructive`, so the tone stays a signal", () => {
    const calm = AGENT_STATES.filter((state) => !ATTENTION_STATES.includes(state));
    for (const state of calm) {
      expect(agentTone(state), `agentTone(${state})`).not.toBe("destructive");
    }
  });
});

describe("every axis answers for its whole union", () => {
  // A missing key would be `undefined` at runtime and render an unstyled badge;
  // the tables are `Record<Union, …>` so this pins the union list itself, which
  // is the part a hand-written test can drift on.
  it("has a tone for every agent state and workspace status", () => {
    for (const state of AGENT_STATES) expect(agentTone(state)).toBeTruthy();
    for (const status of WORKSPACE_STATUSES) expect(statusTone(status)).toBeTruthy();
  });
});
