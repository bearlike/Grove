import { readFileSync } from "node:fs";
import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { ComposerEffort } from "@/components/grove/workspace/composer-effort";
import { EffortMark } from "@/components/grove/effort-icon";

const controls = vi.hoisted(() => ({
  data: { efforts: ["low", "medium", "high", "xhigh", "max"] as string[] },
}));

vi.mock("@/lib/grove/hooks", () => ({
  useSessionControls: () => ({ data: controls.data, isPending: false }),
  useSwitchEffort: () => ({ isPending: false, mutate: vi.fn() }),
}));

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

// The vendored selector portals its content and mounts it only while open, so a
// static render cannot see inside it. The stand-in renders every part in place,
// which is what lets the OPTIONS be asserted at all; the real selector is
// covered by the source census below.
vi.mock("@/components/assistant-ui/model-selector", () => {
  const Part = (slot: string) =>
    function Part({
      children,
      model,
      ...rest
    }: {
      readonly children?: ReactNode;
      readonly model?: { id: string; name: string; icon?: ReactNode };
    }) {
      const {
        variant: _v,
        size: _s,
        align: _a,
        searchable: _se,
        ...props
      } = rest as Record<string, unknown>;
      return (
        <div data-slot={slot} {...(props as object)}>
          {model ? (
            <>
              {model.icon}
              <span>{model.name}</span>
            </>
          ) : null}
          {children}
        </div>
      );
    };
  return {
    ModelSelector: {
      Root: Part("root"),
      Trigger: Part("trigger"),
      Content: Part("content"),
      Search: Part("search"),
      List: Part("list"),
      Empty: Part("empty"),
      Group: Part("group"),
      Item: Part("item"),
    },
  };
});

describe("ComposerEffort", () => {
  it("offers exactly the levels the session reports, each with its own mark", () => {
    const html = renderToStaticMarkup(<ComposerEffort workspaceId="ws-1" />);
    expect(html).toContain('data-testid="composer-effort-trigger"');
    for (const label of ["Low", "Medium", "High", "Extra high", "Max"]) {
      expect(html).toContain(`<span>${label}</span>`);
    }
    // Five rows plus the trigger's own resting mark (the control's brain).
    expect(html.match(/data-testid="effort-mark"/g)?.length).toBe(6);
    expect(html.match(/data-level="(low|medium|high|xhigh|max)"/g)).toHaveLength(5);
  });

  it("claims no active level before one is requested", () => {
    const html = renderToStaticMarkup(<ComposerEffort workspaceId="ws-1" />);
    expect(html).toContain("The agent has not been asked to change it");
    expect(html).toContain(">Effort<");
  });

  it("renders nothing when the session lists no levels", () => {
    controls.data = { efforts: [] };
    expect(renderToStaticMarkup(<ComposerEffort workspaceId="ws-1" />)).toBe("");
    controls.data = { efforts: ["low", "medium", "high", "xhigh", "max"] };
  });
});

describe("EffortMark", () => {
  it("gives every adapter level a distinct rung and an unknown one the brain", () => {
    const rungs = ["minimal", "low", "medium", "high", "xhigh", "max"].map((level) =>
      renderToStaticMarkup(<EffortMark level={level} />),
    );
    expect(new Set(rungs).size).toBe(rungs.length);
    expect(renderToStaticMarkup(<EffortMark level="mystery" />)).toContain("lucide-brain");
  });
});

describe("where the control is mounted", () => {
  it("is composed into the workspace composer's action group, next to the model", () => {
    const source = readFileSync("components/grove/workspace/composer.tsx", "utf8");
    expect(source.length).toBeGreaterThan(500);
    const model = source.indexOf("<ComposerModel ");
    const effort = source.indexOf("<ComposerEffort ");
    expect(model).toBeGreaterThan(-1);
    expect(effort).toBeGreaterThan(model);
  });

  it("claims nothing about the effect: the toast says delivered and carries no description", () => {
    const source = readFileSync("components/grove/workspace/composer-effort.tsx", "utf8");
    const code = source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
    expect(code).toContain('toast.success("Effort request delivered")');
    expect(code).not.toMatch(/next turn|Applies from|has been set|now (on|at)/i);
  });

  it("composes the vendored selector and states no colour of its own", () => {
    const source = readFileSync("components/grove/workspace/composer-effort.tsx", "utf8");
    expect(source).toContain('from "@/components/assistant-ui/model-selector"');
    expect(source).not.toMatch(/\b(bg|text|border)-(red|blue|green|amber|zinc|slate)-/);
  });
});
