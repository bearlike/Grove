import { describe, expect, it } from "vitest";

import { liveLabel, liveStepName } from "@/lib/grove/adapters";
import { formatTokenCount } from "@/lib/grove/adapters/context";
import type { LivePulseView } from "@/lib/grove/api";

const T0 = Date.parse("2026-10-10T12:00:00Z");

function pulse(overrides: Partial<LivePulseView>): LivePulseView {
  return { since: "2026-10-10T12:00:00Z", subagents: 0, ...overrides };
}

describe("liveLabel", () => {
  it("names thinking with the harness's own running estimate and its elapsed time", () => {
    expect(liveLabel(pulse({ step: "thinking", thinking_tokens: 10_100 }), T0 + 14_000)).toBe(
      "Thinking · 10.1K tokens · 14s",
    );
  });

  it("counts the reply being written", () => {
    expect(liveLabel(pulse({ step: "generating", output_tokens: 3_420 }), T0 + 2_000)).toBe(
      "Writing · 3.4K tokens · 2s",
    );
  });

  it("names the tool in flight and its one-line hint", () => {
    expect(
      liveLabel(pulse({ step: "tool", tool: "Bash", tool_detail: "make lint" }), T0 + 8_000),
    ).toBe("Running Bash · make lint · 8s");
  });

  it("says how many sub-agents run and what the newest one is doing", () => {
    expect(
      liveLabel(
        pulse({ step: "subagent", subagents: 2, subagent: "Inventory fields", subagent_tool: "Grep" }),
        T0,
      ),
    ).toBe("2 sub-agents · Inventory fields · Grep");
    expect(liveLabel(pulse({ step: "subagent", subagents: 1 }), T0)).toBe("Sub-agent");
  });

  it("states only what the stream said — no zero counts, no zero clock", () => {
    // A thinking step with no estimate yet, observed in its first second.
    expect(liveLabel(pulse({ step: "thinking", thinking_tokens: null }), T0 + 300)).toBe("Thinking");
    expect(liveLabel(pulse({ step: "thinking", thinking_tokens: 0 }), T0)).toBe("Thinking");
  });

  it("returns null when no step is named, so the loader keeps its plain word", () => {
    expect(liveLabel(pulse({ step: null }), T0)).toBeNull();
    expect(liveStepName(pulse({ step: null }))).toBeNull();
  });

  it("gives a screen reader the step, never a number that moves every second", () => {
    expect(liveStepName(pulse({ step: "thinking", thinking_tokens: 999 }))).toBe("Thinking");
    expect(liveStepName(pulse({ step: "tool", tool: "Read" }))).toBe("Running Read");
  });
});

describe("formatTokenCount", () => {
  it("rounds a moving figure to one decimal on the shared unit ladder", () => {
    expect(formatTokenCount(950)).toBe("950");
    expect(formatTokenCount(10_149)).toBe("10.1K");
    expect(formatTokenCount(2_450_000)).toBe("2.5M");
    // The ladder promotes rather than printing "1,000K".
    expect(formatTokenCount(999_990)).toBe("1M");
  });
});
