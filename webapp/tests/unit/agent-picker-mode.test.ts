import { describe, expect, it } from "vitest";

import { agentSummary } from "@/tests/fixtures/agents";
import { agentDescription } from "@/components/grove/launch/controls/agent-pill";

/** The tool label owns identity; rows do not repeat its transport mode. */
describe("agentDescription", () => {
  const base = agentSummary("base", "claude_code");

  it("omits an empty description rather than restating the mode", () => {
    expect(agentDescription({ ...base, name: "claude", description: "", native: true })).toBeUndefined();
    expect(
      agentDescription({ ...base, name: "claude-terminal", description: "", native: false }),
    ).toBeUndefined();
  });

  it("keeps at most five description words without a transport suffix", () => {
    expect(
      agentDescription({ ...base, name: "gw", description: "Claude via gateway", native: true }),
    ).toBe("Claude via gateway");
    expect(
      agentDescription({ ...base, name: "long", description: "one two three four five six", native: true }),
    ).toBe("one two three four five");
  });
});
