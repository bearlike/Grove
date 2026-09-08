import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { code } from "./_source";

const workspaceControls = [
  "components/grove/workspace/controls-tab.tsx",
  "components/grove/workspace/send-keys.tsx",
  "components/grove/workspace/share-card.tsx",
] as const;

const opaqueFocus =
  "border focus-visible:border-ring focus-visible:ring-ring/50";

const helpHint = readFileSync("components/grove/workspace/help-hint.tsx", "utf8");

describe("workspace control focus", () => {
  it("keeps every acting control visibly bounded when focused", () => {
    for (const path of workspaceControls) {
      expect(readFileSync(path, "utf8"), path).toContain(opaqueFocus);
    }
  });

  /**
   * QUIET AT REST, BOUNDED ON FOCUS — the pair, because taking one without the
   * other is exactly the mistake this pins. The help trigger used to paint a
   * border at all times, which is what made a help mark read as a second square
   * action beside every label; dropping the border outright made it quiet AND
   * silently removed the focus indicator, caught by the e2e focus census rather
   * than by review. `border-transparent` still reserves the width, so the ring
   * has something to colour and nothing shifts when it appears.
   */
  it("keeps the help trigger quiet at rest and bounded on focus", () => {
    expect(helpHint).toContain("border border-transparent");
    expect(helpHint).toContain("focus-visible:border-ring");
    expect(helpHint).toContain("focus-visible:ring-ring/50");
    expect(helpHint).not.toMatch(/focus-visible:ring-0|outline-none/);
  });

  /**
   * A hit target is not a text size. `size-6` is 1.5rem, which at the 80%
   * fine-pointer root renders 19.2px rather than 24 — so the explicit `min-*`
   * pair in px is what actually holds the target, and it must NOT scale with a
   * density lever aimed at type.
   */
  it("holds the 24px pointer target the shrunken glyph does not", () => {
    expect(helpHint).toContain("min-h-[24px]");
    expect(helpHint).toContain("min-w-[24px]");
  });
});

describe("help is a native icon, never a typed glyph", () => {
  it("draws Lucide's InfoIcon inside the vendored tooltip trigger", () => {
    expect(helpHint).toContain('from "lucide-react"');
    expect(helpHint).toContain("InfoIcon");
    expect(helpHint).toContain("TooltipIconButton");
  });

  /**
   * The regression this file exists for: four helpers each rendered the literal
   * string `(i)` where the icon belongs. A census over the callers, because the
   * defect was never in one file — it was in all four independently.
   *
   * Comments are blanked first — see `_source.ts` for why that is load-bearing
   * rather than tidy. Found the hard way: the docstring above failed this very
   * assertion on its first run.
   */
  it("leaves no literal bracketed glyph in any help caller", () => {
    const scanned = [...workspaceControls, "components/grove/workspace/help-hint.tsx"];
    for (const path of scanned) {
      expect(code(path), path).not.toContain("(i)");
    }
    expect(scanned).toHaveLength(4);
  });

  it("keeps each helper's own accessible name rather than one generic label", () => {
    expect(helpHint).toContain("aria-label={`About ${label}`}");
    for (const path of workspaceControls) {
      const source = readFileSync(path, "utf8");
      expect(source, path).toContain("HelpLabel");
      // Every trigger still carries its own caveat, never a shared sentence.
      expect(source.match(/tooltip=/g)?.length ?? 0, path).toBeGreaterThan(0);
    }
  });
});
