import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { FileEditDiff } from "@/components/grove/workspace/file-edit-part";
import type { FileEditPartData } from "@/lib/grove/adapters";

function edit(oldText: string, newText: string): FileEditPartData {
  return { path: "/w/src/app.ts", displayPath: "src/app.ts", oldText, newText, tool: null };
}

/** The rendered row shape, which is the contract — `data-diff-view` only names it. */
function rows(data: FileEditPartData): { view: string | null; split: number; unified: number } {
  const html = renderToStaticMarkup(<FileEditDiff data={data} />);
  return {
    view: html.match(/data-diff-view="(\w+)"/)?.[1] ?? null,
    split: html.split('data-slot="diff-viewer-split-line"').length - 1,
    unified: html.split('data-slot="diff-viewer-line"').length - 1,
  };
}

describe("an expanded edit names its file in full", () => {
  it("restates the whole path and the tally above the diff", () => {
    // The step's own trigger shows the BASENAME, which is right for scanning a
    // run and useless the moment somebody opens it to find out which `page.tsx`
    // this was. The expanded body carries the directory too.
    const html = renderToStaticMarkup(
      <FileEditDiff data={edit("alpha\n", "beta\n")} />,
    );
    expect(html).toContain('data-testid="file-edit-header"');
    expect(html).toContain("src/");
    expect(html).toContain("app.ts");
    expect(html).toContain('data-testid="file-row-stats"');
  });

  it("draws no second disclosure, because the step is already one", () => {
    const html = renderToStaticMarkup(
      <FileEditDiff data={edit("alpha\n", "beta\n")} />,
    );
    expect(html).not.toContain('data-testid="file-edit-card"');
    expect(html).not.toContain('data-testid="file-edit-toggle"');
  });
});

describe("a creation has no left-hand side", () => {
  it("draws a new file unified, with one row per line and no split rows at all", () => {
    // The fixture is what makes this non-vacuous: three added lines and NO
    // removals, so a split renderer would emit three rows too — the assertion
    // that discriminates is which SLOT they carry, not how many there are.
    const created = rows(edit("", "alpha\nbeta\ngamma\n"));
    expect(created.view).toBe("unified");
    expect(created.split).toBe(0);
    expect(created.unified).toBe(3);
  });

  it("treats a whitespace-only original as empty", () => {
    expect(rows(edit("\n  \n", "alpha\n")).view).toBe("unified");
  });

  it("keeps a real two-sided change in split view", () => {
    // Deliberately a REPLACEMENT (one del + one add), the case that only reads
    // correctly side by side. A test fed a pure append would pass against a
    // component that always chose unified.
    const changed = rows(edit("alpha\nbeta\n", "alpha\nBETA\n"));
    expect(changed.view).toBe("split");
    expect(changed.unified).toBe(0);
    expect(changed.split).toBeGreaterThan(0);
  });

  it("splits an overwrite of a file that had content, whatever the tool was called", () => {
    // `Write` is routinely an overwrite, so the discriminator is the OLD TEXT
    // and never the provider's verb. Nothing in this data names a tool.
    expect(rows(edit("old one\nold two\n", "new one\nnew two\n")).view).toBe("split");
  });
});
