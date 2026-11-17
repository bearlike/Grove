import { readFileSync } from "node:fs";
import { join } from "node:path";

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  AddProjectDialog,
  CandidateTable,
  MethodChoices,
  summaryLine,
} from "@/components/grove/projects/add-project-dialog";
import type { ProjectCandidateView } from "@/lib/grove/api";

/**
 * WHAT THIS FILE CAN SEE. Radix mounts `DialogContent` only while open, and a
 * static render has no portal target, so the assembled wizard never appears in
 * this markup. The pieces that carry the issue's real contract are rendered
 * ALONE instead — the method choices, the candidate table — and the wiring
 * (one mount in the shell, the glyph vocabulary, sequential adds) is pinned as
 * a source census. Stepping through the wizard is the browser's to verify.
 */

const ROOT = "/home/demo/Projects";
const SOURCE = readFileSync(
  join(import.meta.dirname, "../../components/grove/projects/add-project-dialog.tsx"),
  "utf8",
);

const widgets: ProjectCandidateView = { name: "widgets", path: `${ROOT}/widgets`, remote: "acme/widgets" };
const gadgets: ProjectCandidateView = { name: "gadgets", path: `${ROOT}/gadgets`, remote: null };

describe("closed dialog", () => {
  it("renders nothing and fetches nothing while closed", () => {
    // No QueryClientProvider here: a closed wizard that called a query hook
    // would throw, which is the assertion — the scan runs only once opened.
    expect(renderToStaticMarkup(<AddProjectDialog />)).toBe("");
  });
});

describe("MethodChoices", () => {
  const html = renderToStaticMarkup(<MethodChoices found={12} onChoose={() => undefined} />);

  it("offers the three methods in order, each with its consequence", () => {
    const order = ["add-project-method-create", "add-project-method-clone", "add-project-method-adopt"].map(
      (id) => html.indexOf(id),
    );
    expect(order.every((i) => i > -1)).toBe(true);
    expect([...order].sort((a, b) => a - b)).toEqual(order);
    expect(html).toContain("Start an empty git repository");
    expect(html).toContain("From any Git URL your git can reach");
  });

  it("states the found count on the existing-repository choice once known", () => {
    expect(html).toContain("Already on disk, not in Grove (12 found)");
    const unknown = renderToStaticMarkup(<MethodChoices found={null} onChoose={() => undefined} />);
    expect(unknown).toContain("Already on disk, not in Grove");
    expect(unknown).not.toContain("found)");
  });

  it("leads each choice with its own glyph, hidden from assistive tech", () => {
    for (const glyph of ["lucide-folder-plus", "lucide-git-fork", "lucide-folder-search"]) {
      expect(html).toMatch(new RegExp(`<svg[^>]*${glyph}[^>]*aria-hidden="true"`));
    }
  });
});

describe("CandidateTable", () => {
  const html = renderToStaticMarkup(
    <CandidateTable candidates={[widgets, gadgets]} selected={new Set([widgets.path])} onSelected={() => undefined} />,
  );

  it("gives every row a labelled native checkbox reflecting the selection", () => {
    const box = (name: string): string =>
      html.match(new RegExp(`<input type="checkbox"[^>]*aria-label="Select ${name}"[^>]*>`))?.[0] ?? "";
    // Found first, so the absence check below cannot pass on a missing element.
    expect(box("widgets")).not.toBe("");
    expect(box("gadgets")).not.toBe("");
    expect(box("widgets")).toContain('checked=""');
    expect(box("gadgets")).not.toContain("checked");
  });

  it("types each row: project over location, never an untyped string", () => {
    expect(html).toContain('data-testid="entity-project"');
    expect(html).toContain('data-testid="entity-location"');
    expect(html).toContain(`${ROOT}/gadgets`);
  });

  it("draws an absent remote as a state with its own mark", () => {
    expect(html).toContain("No remote");
    expect(html).toContain("lucide-circle-dashed");
    expect(html).toContain("acme/widgets");
  });

  it("says Selected in words beside the shape, so colour never carries it alone", () => {
    expect(html).toContain("lucide-square-check");
    expect(html).toContain("Selected");
    expect(html).toContain('data-state="selected"');
  });

  it("bounds only the rows and keeps the header sticky", () => {
    expect(html).toContain('data-slot="scroll-area"');
    expect(html).toContain("max-h-72");
    expect(html).toContain("[&amp;_thead]:sticky");
    expect(html).toContain("Repository / local path");
  });
});

describe("summaryLine", () => {
  it("prints matching and selected, and hidden only when a filter hides a choice", () => {
    expect(summaryLine({ matching: 12, selected: 2, hidden: 0 })).toBe("12 matching repositories · 2 selected");
    expect(summaryLine({ matching: 1, selected: 2, hidden: 1 })).toBe(
      "1 matching repository · 2 selected · 1 hidden",
    );
  });
});

describe("source census", () => {
  it("spells every lucide import with the Icon suffix, multi-line imports included", () => {
    const block = SOURCE.match(/import\s*\{([^}]*)\}\s*from\s*"lucide-react"/)?.[1] ?? "";
    const names = block.split(",").map((n) => n.trim()).filter(Boolean);
    expect(names.length).toBeGreaterThan(5);
    expect(names.filter((n) => !n.endsWith("Icon"))).toEqual([]);
  });

  it("composes only vendored parts and the shared entity atoms", () => {
    for (const part of [
      "@/components/ui/dialog",
      "@/components/ui/scroll-area",
      "@/components/ui/table",
      "@/components/ui/button",
      "@/components/ui/input",
      "@/components/ui/label",
      "@/components/grove/entity",
    ]) {
      expect(SOURCE).toContain(`from "${part}"`);
    }
    // The entity glyphs come from entity.tsx, never re-chosen here.
    expect(SOURCE).not.toMatch(/\bMapPinIcon\b|\bFolderGit2Icon\b/);
  });

  it("runs multiple adds sequentially through mutateAsync, never in parallel", () => {
    expect(SOURCE).toMatch(/for \(const request of adoptRequests\(paths\)\)[\s\S]{0,80}await add\.mutateAsync\(request\)/);
    expect(SOURCE).not.toContain("Promise.all");
  });

  it("is mounted exactly once, in the shell", () => {
    const shell = readFileSync(join(import.meta.dirname, "../../components/grove/shell/app-shell.tsx"), "utf8");
    expect(shell.match(/<AddProjectDialog \/>/g)).toHaveLength(1);
  });

  it("names its primary action by effect, never Continue", () => {
    expect(SOURCE).toContain('action="Create"');
    expect(SOURCE).toContain('action="Clone"');
    expect(SOURCE).toContain("adoptActionLabel(adoptPaths.length)");
    expect(SOURCE).not.toMatch(/>\s*Continue\s*</);
  });
});

describe("dialog height", () => {
  it("is bounded to the viewport so the title and actions stay reachable", () => {
    const source = readFileSync(
      join(process.cwd(), "components/grove/projects/add-project-dialog.tsx"),
      "utf8",
    );
    const content = source.slice(source.indexOf("<DialogContent"), source.indexOf("data-testid=\"add-project-dialog\""));
    expect(content.length).toBeGreaterThan(0);
    expect(content).toContain("max-h-[90dvh]");
    expect(content).toContain("overflow-y-auto");
  });
});
