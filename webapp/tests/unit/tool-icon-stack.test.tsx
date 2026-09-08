import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ToolTimeline } from "@/components/grove/workspace/tool-timeline";

/** The collapsed trigger's rendered markup — the stack only draws while closed. */
function stack(icons: readonly string[]): { discs: number; more: string | null } {
  const html = renderToStaticMarkup(
    <ToolTimeline
      open={false}
      onOpenChange={() => {}}
      streaming={false}
      label="n steps"
      icons={icons}
      stats={[]}
      status={null}
    >
      {null}
    </ToolTimeline>,
  );
  return {
    discs: html.split("tool-icon-stack-disc").length - 1,
    more: html.match(/tool-icon-stack-more[^>]*>\+(\d+)</)?.[1] ?? null,
  };
}

const SLUGS = Array.from({ length: 9 }, (_, i) => `flat-color-icons:slug-${i}`);

describe("the source stack is bounded", () => {
  it("draws one disc per mark while the run stays under the cap", () => {
    expect(stack(SLUGS.slice(0, 3))).toEqual({ discs: 3, more: null });
    expect(stack(SLUGS.slice(0, 5))).toEqual({ discs: 5, more: null });
  });

  it("caps the marks and states the remainder, so the pill's width cannot grow", () => {
    // Nine distinct tools is an ordinary long run. Without the cap this is nine
    // discs wide; the counter is what keeps a summary row's leading element a
    // fixed size whatever the run did.
    const nine = stack(SLUGS);
    expect(nine.more).toBe("4");
    // Five marks plus the counter, which is itself a disc so the silhouette
    // stays one shape.
    expect(nine.discs).toBe(6);
  });

  it("draws nothing at all for a run whose steps had no marks", () => {
    expect(stack([])).toEqual({ discs: 0, more: null });
  });
});
