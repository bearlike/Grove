import { describe, expect, it } from "vitest";

import { attentionLabel } from "@/components/grove/fleet/tokens";
import type { AgentState } from "@/components/grove/fleet/types";

/**
 * Attention is a separate rail mark, never a hue borrowed by a phase shape.
 * This pins the copy table's exact coverage: the daemon can only request
 * attention for these three agent states.
 */
const ATTENTION_STATES: readonly AgentState[] = ["waiting", "blocked", "error"];

describe("rail attention labels", () => {
  it("names every human-action state and no calm state", () => {
    for (const state of ATTENTION_STATES) {
      expect(attentionLabel(state), `attentionLabel(${state})`).toBeTruthy();
    }
    expect(attentionLabel("working")).toBeUndefined();
    expect(attentionLabel("idle")).toBeUndefined();
  });

  it("keeps each human action distinct in words", () => {
    const labels = ATTENTION_STATES.map((state) => attentionLabel(state));
    expect(new Set(labels).size).toBe(ATTENTION_STATES.length);
  });
});
