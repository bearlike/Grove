import { describe, expect, it } from "vitest";

import {
  LAUNCH_INITIAL_VALUES,
  launchReducer,
  type LaunchState,
} from "@/components/grove/launch/launch-state";

/**
 * The session mode's default belongs to the ROSTER ENTRY, so a choice made
 * against one agent must not carry to the next — the same reason a model
 * chosen for one agent is forgotten when the agent changes (RULE 1).
 */
const initial: LaunchState = { values: LAUNCH_INITIAL_VALUES, touched: new Set() };

describe("session mode follows the agent", () => {
  it("is touched when set, and rides as a real value", () => {
    const next = launchReducer(initial, { type: "set", values: { native: false } });
    expect(next.values.native).toBe(false);
    expect(next.touched.has("native")).toBe(true);
  });

  it("is forgotten — value and touch — when the agent changes", () => {
    const chosen = launchReducer(initial, { type: "set", values: { native: false } });
    const switched = launchReducer(chosen, { type: "set", values: { agentName: "codex" } });
    expect(switched.values.native).toBeNull();
    expect(switched.touched.has("native")).toBe(false);
  });

  it("survives an agent change that sets the mode in the same action", () => {
    const both = launchReducer(initial, {
      type: "set",
      values: { agentName: "codex", native: true },
    });
    expect(both.values.native).toBe(true);
    expect(both.touched.has("native")).toBe(true);
  });
});
