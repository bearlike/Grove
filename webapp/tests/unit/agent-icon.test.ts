import { describe, it, expect } from "vitest";
import { Bot } from "lucide-react";
import { siAnthropic, siClaude } from "simple-icons";
import { resolveAgentIcon } from "@/lib/grove/agent-icon";

describe("resolveAgentIcon", () => {
  it("resolves claude to the Claude brand mark", () => {
    const r = resolveAgentIcon("claude");
    expect(r.kind).toBe("brand");
    if (r.kind !== "brand") throw new Error("expected brand");
    expect(r.icon).toBe(siClaude);
    expect(r.hex).toBe(`#${siClaude.hex}`);
    expect(r.label).toBe("Claude Code");
  });

  it("resolves the claude_code adapter_kind to the Claude brand mark", () => {
    const r = resolveAgentIcon("some-model", "claude_code");
    expect(r.kind).toBe("brand");
    if (r.kind !== "brand") throw new Error("expected brand");
    expect(r.icon).toBe(siClaude);
  });

  it("resolves anthropic to the Anthropic brand mark", () => {
    const r = resolveAgentIcon("anthropic");
    expect(r.kind).toBe("brand");
    if (r.kind !== "brand") throw new Error("expected brand");
    expect(r.icon).toBe(siAnthropic);
  });

  it("resolves codex to the lucide Bot fallback", () => {
    const r = resolveAgentIcon("codex");
    expect(r.kind).toBe("lucide");
    if (r.kind !== "lucide") throw new Error("expected lucide");
    expect(r.Icon).toBe(Bot);
    expect(r.label).toBe("Codex");
  });

  it("resolves an unknown agent to the generic lucide Bot", () => {
    const r = resolveAgentIcon("totally-unknown-agent");
    expect(r.kind).toBe("lucide");
    if (r.kind !== "lucide") throw new Error("expected lucide");
    expect(r.Icon).toBe(Bot);
    expect(r.label).toBe("Agent");
  });

  it("matches the agent name case-insensitively by substring", () => {
    const r = resolveAgentIcon("Claude-Sonnet-4");
    expect(r.kind).toBe("brand");
    if (r.kind !== "brand") throw new Error("expected brand");
    expect(r.icon).toBe(siClaude);
  });

  it("gives adapter_kind precedence over the agent name", () => {
    // Name says claude, but adapter_kind says codex → codex wins.
    const r = resolveAgentIcon("claude", "codex");
    expect(r.kind).toBe("lucide");
    if (r.kind !== "lucide") throw new Error("expected lucide");
    expect(r.Icon).toBe(Bot);
    expect(r.label).toBe("Codex");
  });
});
