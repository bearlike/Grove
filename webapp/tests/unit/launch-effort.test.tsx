import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { buildCreateRequest, effortLabel } from "@/lib/grove/adapters/launch";
import {
  LAUNCH_INITIAL_VALUES,
  launchReducer,
  type LaunchState,
} from "@/components/grove/launch/launch-state";

/**
 * The reasoning effort rides the create only when chosen, and its levels belong
 * to the agent kind, so a choice made against Claude Code must not reach Codex
 * (whose ladder differs) or a shell (which the engine refuses with any value).
 */
const initial: LaunchState = { values: LAUNCH_INITIAL_VALUES, touched: new Set(), seededRepo: null };
const ready: LaunchState = launchReducer(initial, {
  type: "seed",
  values: { repoRoot: "/repos/grove", selectedProjectCwd: "/repos/grove", agentName: "claude" },
  repo: "/repos/grove",
});

describe("effort in the create request", () => {
  it("is omitted untouched, so the tool's own default applies", () => {
    const request = buildCreateRequest(ready, "Ship it");
    expect("effort" in request).toBe(false);
  });

  it("carries a chosen level verbatim, in the tool's own spelling", () => {
    const chosen = launchReducer(ready, { type: "set", values: { effort: "xhigh" } });
    expect(buildCreateRequest(chosen, "Ship it").effort).toBe("xhigh");
  });

  it("is omitted again once the choice goes back to the agent default", () => {
    const chosen = launchReducer(ready, { type: "set", values: { effort: "high" } });
    const cleared = launchReducer(chosen, { type: "set", values: { effort: null } });
    expect("effort" in buildCreateRequest(cleared, "Ship it")).toBe(false);
  });

});

describe("effort follows the agent", () => {
  it("is forgotten, value and touch, when the agent changes", () => {
    const chosen = launchReducer(ready, { type: "set", values: { effort: "max" } });
    const switched = launchReducer(chosen, { type: "set", values: { agentName: "codex" } });
    expect(switched.values.effort).toBeNull();
    expect(switched.touched.has("effort")).toBe(false);
    expect("effort" in buildCreateRequest(switched, "Ship it")).toBe(false);
  });

  it("survives an agent change that sets the effort in the same action", () => {
    const both = launchReducer(ready, { type: "set", values: { agentName: "codex", effort: "minimal" } });
    expect(both.values.effort).toBe("minimal");
  });
});

describe("effortLabel", () => {
  it("capitalizes a word and spells out the one level that is not one", () => {
    expect(["minimal", "low", "medium", "high", "xhigh", "max"].map(effortLabel)).toEqual([
      "Minimal",
      "Low",
      "Medium",
      "High",
      "Extra high",
      "Max",
    ]);
  });
});

describe("the effort pill", () => {
  const source = readFileSync("components/grove/launch/controls/effort-pill.tsx", "utf8");
  const surface = readFileSync("components/grove/launch/launch-composer.tsx", "utf8");

  it("reads its levels from the agent summary, never a hard-coded ladder", () => {
    expect(source).toContain(".efforts");
    expect(source).not.toMatch(/"xhigh"|"medium"/);
  });

  it("renders nothing for an agent that lists no levels", () => {
    expect(source).toMatch(/if \(efforts\.length === 0\) return null;/);
  });

  it("sits beside the model pill in the composer toolbar", () => {
    expect(surface.indexOf("<ModelPill />")).toBeGreaterThan(-1);
    expect(surface.indexOf("<EffortPill />")).toBeGreaterThan(surface.indexOf("<ModelPill />"));
  });
});
