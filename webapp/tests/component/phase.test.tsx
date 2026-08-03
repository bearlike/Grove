import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { PhaseBadge } from "@/components/workspace/phase-badge";
import { PhaseMeter } from "@/components/workspace/phase-meter";
import type { PhaseView } from "@/lib/grove/types";

// The task-phase axis in its two registers. The contract that matters:
// null renders NOTHING in both (absence is not a state), the compact badge
// carries the fraction while the name lives in the tooltip, and the meter
// renders the ordering as determinate progress — with the step count stated in
// text too, so the bar is never the sole carrier.

function phase(over: Partial<PhaseView> = {}): PhaseView {
  return {
    phase: "implementing",
    note: null,
    updated_at: "2026-07-31T10:00:00Z",
    index: 2,
    total: 6,
    ...over,
  } as PhaseView;
}

describe("PhaseBadge", () => {
  it("renders nothing when no phase is reported", () => {
    const { container } = render(<PhaseBadge phase={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the fill glyph + the 1-based step fraction, name in the tooltip", () => {
    render(<PhaseBadge phase={phase()} />);
    const badge = screen.getByTestId("phase-badge");
    expect(badge).toHaveAttribute("data-phase", "implementing");
    expect(badge).toHaveTextContent("3/6");
    expect(badge).toHaveAttribute("title", "implementing");
    expect(badge).toHaveAccessibleName(/task phase: implementing, step 3 of 6/i);
  });

  it("folds the note into the tooltip rather than the row", () => {
    render(<PhaseBadge phase={phase({ note: "rebasing onto main" })} />);
    const badge = screen.getByTestId("phase-badge");
    expect(badge).toHaveAttribute("title", "implementing — rebasing onto main");
    expect(badge).not.toHaveTextContent("rebasing onto main");
  });

  it("colors the glyph from the sequential ramp, never a categorical hue", () => {
    render(<PhaseBadge phase={phase({ phase: "done", index: 5 })} />);
    const glyph = screen.getByTestId("phase-badge").querySelector("span");
    expect(glyph).toHaveStyle({ color: "var(--phase-done, var(--phase-scoping))" });
  });
});

describe("PhaseMeter", () => {
  it("renders nothing when no phase is reported", () => {
    const { container } = render(<PhaseMeter phase={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("names the phase and states the step in text beside the bar", () => {
    render(<PhaseMeter phase={phase()} />);
    const meter = screen.getByTestId("phase-meter");
    expect(meter).toHaveTextContent("implementing");
    expect(meter).toHaveTextContent("3/6");
  });

  it("drives a determinate progressbar on the honest 6-step scale", () => {
    render(<PhaseMeter phase={phase()} />);
    const bar = screen.getByRole("progressbar", { name: /task phase: implementing/i });
    expect(bar).toHaveAttribute("aria-valuenow", "3");
    expect(bar).toHaveAttribute("aria-valuemax", "6");
    expect(bar).toHaveAttribute("aria-valuetext", "step 3 of 6");
  });

  it("fills completely at the terminal phase", () => {
    render(<PhaseMeter phase={phase({ phase: "done", index: 5 })} />);
    const bar = screen.getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "6");
    expect(bar.querySelector("div")).toHaveStyle({ transform: "translateX(-0%)" });
  });

  it("shows the note here — the detail register has room the badge does not", () => {
    render(<PhaseMeter phase={phase({ note: "waiting on CI" })} />);
    expect(screen.getByTestId("phase-note")).toHaveTextContent("waiting on CI");
  });

  it("reports WHEN the phase was claimed — a stale claim is the stalled-run tell", () => {
    render(<PhaseMeter phase={phase()} />);
    expect(screen.getByTestId("phase-meter")).toHaveTextContent(/reported/i);
  });
});
