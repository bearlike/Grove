import { describe, expect, it } from "vitest";

import {
  agentGlyph,
  attentionLabel,
  phaseGlyph,
} from "@/components/grove/fleet/tokens";
import type { AgentState, TaskPhase } from "@/components/grove/fleet/types";

const PHASES: readonly TaskPhase[] = [
  "scoping",
  "planning",
  "implementing",
  "verifying",
  "delivering",
  "done",
];
const ATTENTION_STATES: readonly AgentState[] = ["waiting", "blocked", "error"];

function iconName(Icon: React.ElementType): string {
  return (Icon as { displayName?: string; name?: string }).displayName ||
    (Icon as { name?: string }).name ||
    "anonymous";
}

describe("fleet status-mark census", () => {
  it("assigns every task phase a distinct Lucide silhouette", () => {
    const icons = PHASES.map(phaseGlyph).map(iconName);

    expect(icons).toHaveLength(PHASES.length);
    expect(new Set(icons).size).toBe(PHASES.length);
  });

  it("keeps attention states distinct in both shape and accessible label", () => {
    const icons = ATTENTION_STATES.map(agentGlyph).map(iconName);
    const labels = ATTENTION_STATES.map(attentionLabel);

    expect(new Set(icons).size).toBe(ATTENTION_STATES.length);
    expect(labels.every((label) => label !== undefined)).toBe(true);
    expect(new Set(labels).size).toBe(ATTENTION_STATES.length);
  });
});
