import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { RuntimeMark } from "@/components/shared/runtime-mark";
import { RuntimeBadge } from "@/components/workspace/runtime-badge";
import { RUNTIME_GLYPH } from "@/lib/grove/runtime-tokens";

describe("RuntimeMark (the dense register)", () => {
  it.each(["host", "container"] as const)("marks %s — neither state is silent", (runtime) => {
    render(<RuntimeMark runtime={runtime} />);
    const mark = screen.getByTestId("runtime-mark");
    expect(mark.dataset.runtime).toBe(runtime);
    expect(mark.textContent).toBe(RUNTIME_GLYPH[runtime]);
    // Color is an enhancement: the accessible name carries the fact.
    expect(mark.getAttribute("aria-label")).toContain(runtime);
    expect(mark.getAttribute("style")).toContain(`var(--runtime-${runtime}`);
  });
});

describe("RuntimeBadge (the labeled register)", () => {
  it.each(["host", "container"] as const)("labels %s with the shared glyph", (runtime) => {
    render(<RuntimeBadge runtime={runtime} />);
    const badge = screen.getByTestId("runtime-badge");
    expect(badge.dataset.runtime).toBe(runtime);
    expect(badge.dataset.fallback).toBeUndefined();
    expect(badge.textContent).toContain(RUNTIME_GLYPH[runtime]);
    expect(badge.textContent).toContain(runtime);
  });

  it("host is never silent — a bare `return null` for the host case must not come back", () => {
    render(<RuntimeBadge runtime="host" />);
    expect(screen.queryByTestId("runtime-badge")).not.toBeNull();
  });

  it("a fallback stays visibly degraded, never a plain host mark", () => {
    render(<RuntimeBadge runtime="host" runtimeFallbackReason="docker unavailable" />);
    const badge = screen.getByTestId("runtime-badge");
    expect(badge.dataset.fallback).toBe("true");
    expect(badge.textContent).toContain("fallback");
    // The amber degradation tone, distinct from the quiet runtime hue.
    expect(badge.className).toContain("--status-orphaned");
    expect(badge.getAttribute("title")).toContain("docker unavailable");
  });

  it("a default-config container reads as a notice with the graduation path", () => {
    render(<RuntimeBadge runtime="container" runtimeDefaultConfig />);
    const badge = screen.getByTestId("runtime-badge");
    expect(badge.dataset.fallback).toBeUndefined();
    expect(badge.getAttribute("title")).toContain("grove init devcontainer");
  });

  it("the host title names what the boundary's absence means", () => {
    render(<RuntimeBadge runtime="host" />);
    expect(screen.getByTestId("runtime-badge").getAttribute("title")).toContain("credentials");
  });
});
