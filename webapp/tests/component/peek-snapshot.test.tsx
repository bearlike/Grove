import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { PeekSnapshot } from "@/components/workspace/peek-snapshot";

describe("PeekSnapshot", () => {
  it("renders empty placeholder when snapshot is null", () => {
    render(<PeekSnapshot snapshot={null} takenAt={null} />);
    expect(screen.getByTestId("peek-snapshot-empty")).toBeInTheDocument();
  });

  it("renders snapshot text when present", () => {
    render(<PeekSnapshot snapshot={"line1\nline2"} takenAt="2026-05-09T00:00:00Z" />);
    const pre = screen.getByTestId("peek-snapshot");
    expect(pre.textContent).toBe("line1\nline2");
    expect(pre.dataset.takenAt).toBe("2026-05-09T00:00:00Z");
  });

  it("uses monospace font", () => {
    render(<PeekSnapshot snapshot="x" takenAt={null} />);
    expect(screen.getByTestId("peek-snapshot").className).toMatch(/font-mono/);
  });
});
