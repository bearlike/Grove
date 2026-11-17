import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { buildCreateRequest, inheritedModel } from "@/lib/grove/adapters/launch";
import {
  LAUNCH_INITIAL_STATE,
  launchReducer,
  type LaunchState,
} from "@/components/grove/launch/launch-state";

// The saved defaults that broke a create on another agent: a model chosen for the Claude
// entry, which another roster entry refuses at launch.
const SAVED = { agent: "claude-gateway", model: "anthropic-opus" } as const;

/** What the draft's seed effect dispatches for the agent now selected. */
function seed(state: LaunchState): LaunchState {
  const agent = state.values.agentName ?? SAVED.agent;
  return launchReducer(state, {
    type: "seed",
    values: { agentName: SAVED.agent, model: inheritedModel(SAVED, agent) },
  });
}

function pick(state: LaunchState, agentName: string): LaunchState {
  return seed(launchReducer(state, { type: "set", values: { agentName } }));
}

function request(state: LaunchState) {
  const chosen = launchReducer(state, { type: "set", values: { repoRoot: "/repos/smoke" } });
  // The defaults for that project have answered, as they must before Send.
  const ready = launchReducer(chosen, { type: "seed", values: {}, repo: "/repos/smoke" });
  return buildCreateRequest(ready, "List the files");
}

// `seed` above stands in for the component's effect, which needs a query
// client to render. These pin that the real effect makes the same call.
describe("the composer reads the one rule", () => {
  const source = (path: string) => readFileSync(join(process.cwd(), path), "utf8");

  it("seeds the model for the selected agent and re-seeds when it changes", () => {
    const row = source("components/grove/launch/launch-state.tsx");
    expect(row).toContain("model: engineProfilePending ? undefined : inheritedModel(resolved, agentName");
    expect(row).toMatch(/\[defaults\.data, seed, agentName, repo, engineProfilePending\]/);
  });

  it("displays an untouched model through the same rule", () => {
    const pill = source("components/grove/launch/controls/model-pill.tsx");
    expect(pill).toContain("values.model ?? inheritedModel(defaults.data, agentName)");
    expect(pill).not.toContain("defaults.data?.model");
  });
});

describe("inheritedModel", () => {
  it("gives the saved agent its saved model", () => {
    expect(inheritedModel(SAVED, "claude-gateway")).toBe("anthropic-opus");
  });

  it("gives another roster entry nothing", () => {
    expect(inheritedModel(SAVED, "codex")).toBeNull();
  });

  it("keeps a saved model global when no agent is saved", () => {
    expect(inheritedModel({ agent: null, model: "anthropic-opus" }, "codex")).toBe("anthropic-opus");
  });

  it("answers nothing before the defaults arrive", () => {
    expect(inheritedModel(undefined, "codex")).toBeNull();
  });
});

describe("the create payload across an agent switch", () => {
  it("shows the saved model for the saved agent and sends no model field", () => {
    const state = seed(LAUNCH_INITIAL_STATE);
    expect(state.values.agentName).toBe("claude-gateway");
    expect(state.values.model).toBe("anthropic-opus");
    // Untouched: the engine resolves the same answer for the same agent.
    expect("model" in request(state)).toBe(false);
  });

  it("drops the inherited model on a switch away, so the pill and payload agree", () => {
    const state = pick(seed(LAUNCH_INITIAL_STATE), "codex");
    expect(state.values.agentName).toBe("codex");
    expect(state.values.model).toBeNull();
    const body = request(state);
    expect(body.agent_name).toBe("codex");
    expect("model" in body).toBe(false);
  });

  it("recomputes it on a switch back", () => {
    const state = pick(pick(seed(LAUNCH_INITIAL_STATE), "codex"), "claude-gateway");
    expect(state.values.model).toBe("anthropic-opus");
    expect("model" in request(state)).toBe(false);
  });

  it("does not inherit when the agent was picked before the defaults arrived", () => {
    const early = launchReducer(LAUNCH_INITIAL_STATE, { type: "set", values: { agentName: "codex" } });
    const state = seed(early);
    expect(state.values.agentName).toBe("codex");
    expect(state.values.model).toBeNull();
  });

  it("forwards an explicit model on another agent unchanged", () => {
    const chosen = launchReducer(pick(seed(LAUNCH_INITIAL_STATE), "codex"), {
      type: "set",
      values: { model: "claude-sonnet-5-5", customModel: false },
    });
    expect(request(seed(chosen)).model).toBe("claude-sonnet-5-5");
  });
});
