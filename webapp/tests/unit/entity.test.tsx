import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { BranchLabel, LocationLabel, PROJECT_MIN_WIDTH, ProjectLabel } from "@/components/grove/entity";

/**
 * The entity vocabulary is a shared atom: the rail, the fleet card and the
 * sessions table all name a project and a branch, and the whole reason it is
 * one component is that they must not each arrive at their own treatment.
 *
 * Classes are asserted here for the same reason `card.test.tsx` asserts them —
 * the typing IS the rendered markup, and there is no other artifact of it.
 * What is pinned is the CONTRACT a call site depends on, not the choice of
 * glyph: swapping `FolderGit2` for another repo mark is a design decision these
 * tests should not veto.
 */

describe("entity labels", () => {
  it("speaks the kind and hides the glyph, so a screen reader hears a type and not an icon name", () => {
    const html = renderToStaticMarkup(<ProjectLabel name="Grove" />);
    expect(html).toContain("Project: ");
    expect(html).toContain("aria-hidden");
    // The kind must reach the accessibility tree as WORDS. A glyph alone types
    // the line for sighted readers only.
    expect(html).toContain("sr-only");
  });

  it("sizes the glyph in em, so one vocabulary serves a text-xs rail and a text-sm card", () => {
    // The first consumer found this: a fixed 14px mark that reads well on the
    // fleet card is a smudge on the rail's 12px subtitle, and two of them on
    // one line is the visual noise the vocabulary exists to avoid. `em` is what
    // makes "reads the same everywhere" mean the same PROPORTION.
    const html = renderToStaticMarkup(<ProjectLabel name="Grove" />);
    expect(html).toContain("size-[1em]");
    expect(html).not.toMatch(/size-\d/);
  });

  it("lets the text truncate and never the glyph", () => {
    // A clipped branch name is still legible; a clipped icon is a smudge, and
    // it is the part carrying the type.
    const html = renderToStaticMarkup(<BranchLabel name="feature/a-very-long-branch-name" />);
    expect(html).toContain("shrink-0");
    expect(html).toContain("truncate");
    expect(html).toContain("min-w-0");
  });

  it("merges a caller's className instead of stacking a losing duplicate", () => {
    // Raw concatenation emits both classes and lets stylesheet order decide;
    // tailwind-merge is what makes a call site's size actually apply.
    const html = renderToStaticMarkup(<ProjectLabel name="Grove" className="text-xs" />);
    expect(html).toContain("text-xs");
  });

  it("types a branch as an identifier and a project as a name", () => {
    // A branch is something the reader may have to retype; a project name is
    // prose. That difference is deliberate and is half the typing.
    expect(renderToStaticMarkup(<BranchLabel name="main" />)).toContain("font-mono");
    expect(renderToStaticMarkup(<ProjectLabel name="Grove" />)).not.toContain("font-mono");
  });

  it("keeps a location distinct from a project, because a cwd is often not a repo", () => {
    // The session catalog carries rows whose enclosing project is null. Drawing
    // a repo mark over a bare path would assert something the data does not.
    const location = renderToStaticMarkup(<LocationLabel path="/srv/scratch" />);
    expect(location).toContain("Location: ");
    expect(location).not.toContain("Project: ");
  });

  it("carries the full value in a title, so a truncated row is still recoverable", () => {
    expect(renderToStaticMarkup(<BranchLabel name="main" />)).toContain('title="Branch: main"');
  });
});

/**
 * The 8-character floor a project keeps before a branch sharing its row may
 * crowd it out — see `entity.tsx` for why this is a MINIMUM (`min-w`) rather
 * than a cap: a fixed `max-w-*` would clip the project even where its row has
 * room to spare (the mobile fleet sheet renders far wider than the docked
 * rail), where a floor only ever binds when space is actually short.
 *
 * Real layout — whether flexbox actually redirects the deficit onto the
 * branch once the project hits this floor — cannot be proven by an SSR
 * string render; jsdom does no box layout. That was verified in a real
 * browser instead: at a pathologically narrow 60px column, a project WITHOUT
 * this floor was measured shrinking to 43px (well under the 8-character
 * guarantee) while the SAME markup WITH it held at 71px (`1em` icon + the
 * `gap-1` + 8 characters at 12px) and pushed the entire deficit onto the
 * branch, which shrank to nothing instead. What these tests pin is the
 * CONTRACT that measurement depends on: the constant expresses that formula,
 * and it lands on the project, never the branch.
 */
describe("PROJECT_MIN_WIDTH", () => {
  it("expresses an 8-character floor in ch, plus the icon and its gap", () => {
    // `ch` alone would undercut the guarantee by the glyph's own width — the
    // icon is `size-[1em]` and sits a `gap-1` (`0.25rem`) from the string, so
    // the floor has to cover both or the 8 characters it promises are partly
    // spent on the icon instead of the name.
    expect(PROJECT_MIN_WIDTH).toContain("8ch");
    expect(PROJECT_MIN_WIDTH).toContain("1em");
    expect(PROJECT_MIN_WIDTH).toContain("0.25rem");
  });

  it("overrides the shared min-w-0 rather than stacking a losing duplicate", () => {
    const html = renderToStaticMarkup(<ProjectLabel name="Grove" className={PROJECT_MIN_WIDTH} />);
    expect(html).toContain(PROJECT_MIN_WIDTH);
    expect(html).not.toMatch(/class="[^"]*\bmin-w-0\b/);
  });
});
