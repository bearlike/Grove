import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

/**
 * THREE OPENERS FOR ONE WIZARD, pinned as a source census.
 *
 * Each opener is JSX inside a component that needs a query client, a fleet
 * stream and a popover to render, so there is no artifact a `node`-environment
 * suite can inspect instead. The rail's footer additionally has to sit OUTSIDE
 * `CommandList`: cmdk filters what is inside it, so a search that matches no
 * project would otherwise take the way to add one away with it.
 */
const ENTRIES = [
  {
    file: "components/grove/fleet/project-context.tsx",
    testid: "project-context-add",
  },
  {
    file: "components/grove/launch/controls/project-pill.tsx",
    testid: "launch-project-add",
  },
  {
    file: "components/grove/launch/launch-surface.tsx",
    testid: "launch-add-project",
  },
] as const;

const sources = new Map(ENTRIES.map(({ file }) => [file, readFileSync(file, "utf8")]));

describe("the add-project entry points", () => {
  it("read every file, so a clean result is not an empty scan", () => {
    expect(sources.size).toBe(ENTRIES.length);
    for (const [file, source] of sources) {
      expect(source.length, `${file} read as empty`).toBeGreaterThan(0);
    }
  });

  for (const { file, testid } of ENTRIES) {
    describe(file, () => {
      const source = sources.get(file)!;

      it(`carries data-testid="${testid}"`, () => {
        expect(source).toContain(`data-testid="${testid}"`);
      });

      it("leads with FolderPlusIcon", () => {
        expect(source).toContain("<FolderPlusIcon");
        expect(source).toMatch(/<FolderPlusIcon[^>]*aria-hidden/);
      });

      it("opens the wizard through the shared store", () => {
        expect(source).toContain("add-project-store");
        expect(source).toMatch(/openWizard\(/);
      });
    });
  }

  it("the rail's footer sits after CommandList closes, never inside it", () => {
    const source = sources.get("components/grove/fleet/project-context.tsx")!;
    const listClose = source.indexOf("</CommandList>");
    const footer = source.indexOf('data-testid="project-context-add"');
    expect(listClose).toBeGreaterThan(-1);
    expect(footer).toBeGreaterThan(-1);
    expect(footer).toBeGreaterThan(listClose);
  });

  it("the empty state no longer sends the reader to a CLI command", () => {
    const source = sources.get("components/grove/launch/launch-surface.tsx")!;
    expect(source).not.toContain("grove config add-project");
  });
});
