import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { NestLevel, nestedTriggerClass, useNestDepth } from "@/components/grove/workspace/nesting";

/** Reports its own ambient depth as text, so the contract can be read out of
 * static markup without a testing-library render pass. */
function DepthProbe(): React.ReactNode {
  const depth = useNestDepth();
  return <span data-testid="depth-probe">{depth}</span>;
}

describe("NestLevel — the one depth mechanism the tool-call tree recurses through", () => {
  it("is depth 0 with no wrapper at all — a standalone tool call is unaffected", () => {
    expect(renderToStaticMarkup(<DepthProbe />)).toContain(">0<");
  });

  it("raises depth by exactly one per boundary, recursively, however many are stacked", () => {
    const markup = renderToStaticMarkup(
      <NestLevel>
        <NestLevel>
          <DepthProbe />
        </NestLevel>
      </NestLevel>,
    );
    expect(markup).toContain('data-nest-depth="1"');
    expect(markup).toContain('data-nest-depth="2"');
    expect(markup).toContain(">2<");
  });

  it("indents and connects a level with the same geometry every time — never optional", () => {
    const markup = renderToStaticMarkup(
      <NestLevel>
        <span>child</span>
      </NestLevel>,
    );
    // Indentation, the connector and the ambient type step all ride the SAME
    // wrapper, so the relationship reads even with any one of them removed —
    // design-system §4.7's "never the sole carrier" rule, extended to depth.
    expect(markup).toContain("pl-3");
    expect(markup).toContain("border-l");
    expect(markup).toContain("border-border");
    expect(markup).toContain("text-xs");
  });

  it("never invents a step below the ramp's floor — depth 2 is not smaller than depth 1", () => {
    const markup = renderToStaticMarkup(
      <NestLevel>
        <NestLevel>
          <span>leaf</span>
        </NestLevel>
      </NestLevel>,
    );
    const sizes = new Set(markup.match(/\btext-(?:xs|sm|base)\b/g));
    expect(sizes).toEqual(new Set(["text-xs"]));
  });
});

describe("nestedTriggerClass — the one type step that exists before the floor", () => {
  it("leaves a standalone tool call's vendored text-sm untouched", () => {
    expect(nestedTriggerClass(0)).toBeUndefined();
  });

  it("pulls a nested call's trigger down to the group's own floor at every deeper level", () => {
    expect(nestedTriggerClass(1)).toBe("text-xs");
    expect(nestedTriggerClass(2)).toBe("text-xs");
  });
});
