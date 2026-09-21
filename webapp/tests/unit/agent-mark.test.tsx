import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AgentMark } from "@/components/grove/agent-mark";

/**
 * A mark is wrong in the one way nothing catches: an agent wearing another
 * vendor's logo still renders, still typechecks, and still passes every test
 * that only asserts a mark exists. So these render the real component and read
 * the brand it resolved, rather than asserting on the mapping table in
 * isolation.
 *
 * The concrete defect this pins: a host roster entry named
 * `OpenCode (via KK Gateway)` was drawing the neutral TERMINAL glyph, because
 * `opencode` had no brand of its own and fell through to `generic` — which is
 * how a first-class native adapter came to look like a shell integration in
 * the fleet.
 */
function brandOf(agentName: string): string | null {
  const markup = renderToStaticMarkup(<AgentMark agentName={agentName} />);
  return /data-brand="([^"]+)"/.exec(markup)?.[1] ?? null;
}

describe("AgentMark", () => {
  it("draws each native provider with its own vendor mark", () => {
    expect(brandOf("Claude Code (default)")).toBe("claude");
    expect(brandOf("Codex")).toBe("codex");
    expect(brandOf("OpenCode (via KK Gateway)")).toBe("opencode");
  });

  it("never draws a native provider as the neutral shell glyph", () => {
    // `generic` is the honest answer for a bare shell and a lie for any of
    // these three — it is what the OpenCode row actually rendered before the
    // brand existed.
    for (const name of ["Claude Code (default)", "Codex", "OpenCode (via KK Gateway)"]) {
      expect(brandOf(name)).not.toBe("generic");
    }
  });

  it("still falls back to the neutral glyph for an agent with no vendor", () => {
    // The fallback is deliberate, not a gap: Grove launches shells and
    // in-house agents that have no brand, and inventing one would be worse.
    expect(brandOf("bash")).toBe("generic");
  });

  it("renders a bare svg, so a Button's has-[>svg] padding rule still matches", () => {
    const markup = renderToStaticMarkup(<AgentMark agentName="OpenCode (via KK Gateway)" />);
    expect(markup.startsWith("<svg")).toBe(true);
    expect(markup).toContain('aria-hidden="true"');
  });
});
