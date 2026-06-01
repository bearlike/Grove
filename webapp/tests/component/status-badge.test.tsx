import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { StatusBadge } from "@/components/workspace/status-badge";

describe("StatusBadge", () => {
  it.each([
    ["active", "●", "active"],
    ["idle", "◐", "idle"],
    ["paused", "‖", "paused"],
    ["offline", "○", "offline"],
    ["orphaned", "⊘", "orphaned"],
    ["error", "✗", "error"],
  ])("renders glyph + label for %s", (status, glyph, label) => {
    render(<StatusBadge status={status as never} />);
    const badge = screen.getByTestId("status-badge");
    expect(badge.dataset.status).toBe(status);
    expect(badge.textContent).toContain(glyph);
    expect(badge.textContent).toContain(label);
  });

  it("uses CSS variable for color binding", () => {
    render(<StatusBadge status="active" />);
    const badge = screen.getByTestId("status-badge");
    expect(badge.getAttribute("style")).toContain("var(--status-active)");
  });

  it("active status applies pulse class", () => {
    render(<StatusBadge status="active" />);
    const glyph = screen.getByTestId("status-badge").firstChild as HTMLElement;
    expect(glyph.className).toMatch(/animate-grove-pulse/);
  });

  it("non-active status does NOT pulse", () => {
    render(<StatusBadge status="idle" />);
    const glyph = screen.getByTestId("status-badge").firstChild as HTMLElement;
    expect(glyph.className).not.toMatch(/animate-grove-pulse/);
  });
});
