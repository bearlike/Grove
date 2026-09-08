import { describe, expect, it } from "vitest";

import { agentDescription } from "@/components/grove/launch/controls/agent-pill";

/**
 * Two built-in entries per provider differ ONLY in launch mode, so the picker
 * row has to say which is which — with or without an operator description.
 */
describe("agentDescription", () => {
  const base = { kind: "claude_code" as const, models: [] as string[] };

  it("states the mode alone when the entry has no description", () => {
    expect(agentDescription({ ...base, name: "claude", description: "", native: true })).toBe(
      "Native session",
    );
    expect(
      agentDescription({ ...base, name: "claude-terminal", description: "", native: false }),
    ).toBe("Terminal");
  });

  it("keeps the operator's description first and appends the mode", () => {
    expect(
      agentDescription({ ...base, name: "gw", description: "Claude via gateway", native: true }),
    ).toBe("Claude via gateway · Native session");
  });
});
