import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { PhaseMeter } from "@/components/grove/workspace/phase-meter";
import { FIXTURE_PHASE } from "../e2e/_fixtures";

/**
 * WIDTH CHANGES THE WORDS, NOT THE TASK'S SHAPE.
 *
 * The task card lives in a resizable panel, so labels respond to its container
 * rather than the viewport. Every checkpoint stays labelled: abbreviated below
 * 420px, full words at and above it, while the horizontal sequence stays intact.
 */
function render(): string {
  return renderToStaticMarkup(<PhaseMeter phase={FIXTURE_PHASE} />);
}

describe("phase responsiveness", () => {
  it("uses the task card's container, never the viewport, to exchange label lengths", () => {
    const html = render();

    expect(html).toContain("@min-[420px]/task:hidden");
    expect(html).toContain("@min-[420px]/task:block");
    expect(html).not.toMatch(/\b(?:sm|md|lg|xl|2xl|3xl):(?:hidden|block)/);
  });

  it("keeps six dots on a horizontal ordered track while every label remains available", () => {
    const html = render();
    const track = html.match(
      /<ol class="([^"]*)" aria-label="Task phase">/,
    )?.[1];

    expect(track).toContain("flex");
    expect(track).not.toContain("flex-col");
    expect(track).not.toContain("flex-wrap");
    expect(html.match(/<li /g)).toHaveLength(6);
    expect(html.match(/text-\[10px\]/g)).toHaveLength(6);
    expect(html.match(/@min-\[420px\]\/task:block/g)).toHaveLength(6);
  });

  it("uses shorter words below the container threshold and full words above it", () => {
    const html = render();

    expect(html).toContain("Deliver</span>");
    expect(html).toContain("Delivering</span>");
    expect(html).toContain("Verify</span>");
    expect(html).toContain("Verifying</span>");
  });
});
