import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { PhaseMeter } from "@/components/grove/workspace/phase-meter";
import { FIXTURE_PHASE } from "../e2e/_fixtures";

/**
 * BLOCKED IS AN INTERRUPTED POSITION, NOT A NEW DESTINATION.
 *
 * A reader needs to see both how far the work got and that progress needs
 * attention. Moving the report to a seventh checkpoint, or relying on red alone,
 * turns those two independent facts into one ambiguous state.
 */
function render(blocked: boolean, note: string | null): string {
  return renderToStaticMarkup(
    <PhaseMeter phase={{ ...FIXTURE_PHASE, blocked, note }} />,
  );
}

describe("a blocked task phase", () => {
  it("keeps the reached position and six-step sequence", () => {
    const html = render(true, "Waiting for approval");

    expect(html).toContain('data-phase="delivering"');
    expect(html).toContain("Deliver");
    expect(html.match(/<li /g)).toHaveLength(6);
    expect(html.match(/data-done="true"/g)).toHaveLength(4);
    expect(html.match(/data-current="true"/g)).toHaveLength(1);
  });

  it("carries blocked through an octagon and the word as a warning, not agent-axis red", () => {
    const html = render(true, "Waiting for approval");
    const badge = html.match(
      /<span[^>]*data-testid="phase-blocked"[^>]*>/,
    )?.[0];

    expect(badge).toContain('data-variant="destructive"');
    expect(html).toContain("lucide-octagon-alert");
    expect(html).toContain("blocked");
    expect(html).toContain("Waiting for approval");
  });

  it("tints the report region rather than treating a block as completion", () => {
    const html = render(true, "Waiting for approval");
    const region = html.match(/<div[^>]*data-testid="phase-note"[^>]*>/)?.[0];

    expect(region).toContain("border-destructive/40");
    expect(region).toContain("bg-destructive/5");
    expect(html).not.toContain('data-done="true" aria-current');
  });

  it("keeps a standalone word-and-shape warning when no explanation was reported", () => {
    const html = render(true, "   ");

    expect(html).not.toContain('data-testid="phase-note"');
    expect(html).toContain('data-testid="phase-blocked"');
    expect(html).toContain("lucide-octagon-alert");
    expect(html).toContain("blocked");
  });

  it("does not manufacture a blocked marker for work still moving", () => {
    const html = render(false, "Normal progress");

    expect(html).not.toContain('data-testid="phase-blocked"');
    expect(html).not.toContain("lucide-octagon-alert");
  });
});
