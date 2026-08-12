import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

/**
 * SOURCE assertions, deliberately — these are Tailwind classes on JSX
 * elements, not exported constants, so there is nothing pure to import and
 * unit-test. The contract worth pinning is structural: which literal widths
 * exist at all, whether two files that must agree on a number actually do,
 * and whether a line of chrome carries the elements the design review named.
 * Same style as `transcript-parts.test.tsx`'s port assertions, for the same
 * reason: there is no rendered artifact that proves an absence or a
 * cross-file agreement.
 */
const shell = readFileSync("components/grove/shell/app-shell.tsx", "utf8");
const sidebar = readFileSync("components/grove/shell/app-sidebar.tsx", "utf8");
const header = readFileSync("components/grove/shell/shell-header.tsx", "utf8");
const fleetTree = readFileSync("components/grove/fleet/fleet-tree.tsx", "utf8");
const workPanel = readFileSync("components/grove/workspace/work-panel.tsx", "utf8");

describe("the desktop rail widened twice: 260px → 328px → 392px", () => {
  it("expands to w-98 (392px) — 328px * 1.2 = 393.6px, and Tailwind's spacing unit is 4px", () => {
    expect(shell).toContain('collapsed ? "w-12" : "w-98"');
    expect(shell).not.toContain("w-65");
    expect(shell).not.toContain('"w-82"');
  });

  it("collapsed width is untouched — collapse still narrows to the icon rail", () => {
    expect(shell).toContain('"w-12"');
  });
});

describe("the mobile sheet takes the complete viewport width when expanded", () => {
  it("is not pinned to the desktop rail's measure", () => {
    expect(shell).not.toMatch(/SheetContent[^>]*w-65/);
    expect(shell).not.toMatch(/<SheetContent[^>]*w-8\d/);
    expect(shell).not.toMatch(/<SheetContent[^>]*w-9\d/);
  });

  it("sets w-full and overrides the vendored sm:max-w-sm cap", () => {
    expect(shell).toContain('className="flex w-full flex-col p-0 sm:max-w-none"');
  });
});

/**
 * #57 — the mobile sheet gave the rail near-full viewport width, but the
 * fleet list inside it stayed pinned to the DOCKED rail's own width, which a
 * pixel-for-pixel duplicate of the aside's width had baked in. That produced
 * both symptoms the report named at once: empty space past the docked measure,
 * and rows still truncating as if the column really were only that wide.
 *
 * The fix removes the duplicate literal rather than re-deriving it per
 * context: `w-full` cannot drift from its parent, so it is correct on the
 * docked aside AND inside the sheet (the viewport) by construction, with
 * nothing to keep in sync by hand. That is also why the rail's two widenings
 * needed no change here at all.
 */
describe("the fleet list fills whichever container renders it (#57)", () => {
  it("carries no fixed pixel width of its own", () => {
    // DERIVED from the shell rather than written out, so this assertion cannot
    // go stale the next time the rail is widened — which is exactly the drift
    // that produced #57 in the first place.
    const railWidth = shell.match(/collapsed \? "w-12" : "(w-\d+)"/)?.[1];
    expect(railWidth).toBeDefined();
    expect(fleetTree).not.toContain(`"${railWidth}`);
    expect(fleetTree).not.toMatch(/collapsed \? "w-12/);
  });

  it("is w-full in both the collapsed and expanded states", () => {
    expect(fleetTree).toContain('collapsed ? "w-full overflow-hidden px-2 pt-1"');
    expect(fleetTree).toContain(': "w-full overflow-y-auto p-3"');
  });
});

describe("the rail's brand row shares the header's line, not a row above it", () => {
  it("offsets the brand row by the SAME 8px the gutter gives the panel", () => {
    // app-shell.tsx's gutter is `p-2` (8px), which is what pushes the panel —
    // and ShellHeader's toggle row inside it — 8px down from the viewport
    // top. The rail's aside carries no such padding, so its brand row must
    // add the same offset itself, or the two rows read as different lines.
    expect(shell).toMatch(/p-2 md:pl-0/);
    expect(sidebar).toContain('"mt-2 flex h-12 shrink-0 items-center gap-2"');
  });

  it("both rows are the same height, which is what makes them one line", () => {
    expect(sidebar).toMatch(/\bh-12\b/);
    expect(header).toContain("h-12");
  });

  it("the header line still carries the toggle, the title and nothing portalled in", () => {
    expect(header).toContain('data-testid="shell-sidebar-toggle"');
    expect(header).toMatch(/\{title \?\? sectionFor\(pathname\)\.label\}/);
  });
});

describe("the search box is leaner: full accessible name, lighter visible chrome", () => {
  it("keeps the descriptive aria-label but drops the visible placeholder's weight", () => {
    expect(fleetTree).toContain('aria-label="Search workspaces"');
    expect(fleetTree).toContain('placeholder="Search"');
  });

  it("sizes the input to the rail's own type scale rather than the vendored default", () => {
    expect(fleetTree).toMatch(/<ThreadListSearch[\s\S]*?className="text-xs"/);
  });

});

/**
 * The rail's contents used to begin 8px from each of its edges and run at a
 * 2px rhythm, which is what "too close to either side" and "no structural
 * hierarchy" measured as. Three separate rules now govern it, and each one is
 * pinned here because they are Tailwind classes on JSX, with nothing pure to
 * import.
 */
describe("the rail gutters its contents, and gutters them the same everywhere", () => {
  it("expanded, every band takes the footer's own p-3 — the one nobody called cramped", () => {
    expect(fleetTree).toContain('"w-full overflow-y-auto p-3"');
    expect(sidebar).toContain('collapsed ? "px-2" : "px-3"');
    expect(sidebar).toContain('collapsed ? "p-2" : "p-3"');
  });

  it("collapsed, every band stays px-2 — 8 + a 32px icon + 8 IS the 48px rail", () => {
    // A wider gutter here does not look roomier, it pushes the icons off
    // centre, so the collapsed state is deliberately excluded from all of it.
    expect(fleetTree).toContain('collapsed ? "w-full overflow-hidden px-2 pt-1"');
    expect(shell).toContain('"w-12"');
  });
});

describe("one vertical rhythm down the whole rail, at 3x the old 2px", () => {
  // Every element that separates two ROWS, named individually rather than
  // counted — symmetry is the entire request, so one survivor at the old 2px
  // is the defect, and a count would be satisfied by any four of the five.
  it.each([
    ["the list itself", "relative flex flex-1 flex-col gap-1.5 transition-[padding]"],
    ["the collapse-fade group", "flex min-h-0 flex-1 flex-col gap-1.5 transition-opacity"],
    ["the search and filter row", 'className="flex items-center gap-1.5"'],
    ["the loading skeleton, which is shaped like the rows", 'className="flex flex-col gap-1.5"'],
  ])("steps %s by gap-1.5", (_name, className) => {
    expect(fleetTree).toContain(className);
  });

  it("the footer's destinations run on the same rhythm as the list above them", () => {
    expect(sidebar).toContain("flex shrink-0 flex-col gap-1.5 border-t");
  });

  it("keeps the ONE tighter gap, which is inside a row rather than between two", () => {
    // A workspace row's title line and entity line are one object, and spacing
    // is the only thing grouping them: widened to the row rhythm they would
    // read as two rows rather than one. Tighter-within-looser is what makes
    // the outer rhythm legible at all.
    expect(fleetTree).toContain("flex min-w-0 flex-1 flex-col items-start gap-0.5");
  });
});

describe("the daemon's version and uptime break the rhythm, deliberately", () => {
  it("sits a DOUBLE step below the identity control, not one", () => {
    // The complaint was structural rather than aesthetic: at the uniform gap
    // the service line read as a fifth footer destination instead of as a
    // description of what the four above are served by.
    expect(sidebar).toMatch(/gap-1\.5 empty:hidden", collapsed \? null : "mt-3"/);
  });

  it("pays nothing for the states where DaemonStatus renders nothing at all", () => {
    // It returns null when collapsed and when the daemon is unreachable. A
    // wrapper is a flex item either way, so without `empty:hidden` those two
    // states would spend the gap and the margin on an absent row.
    expect(sidebar).toContain("empty:hidden");
  });
});

/**
 * The work panel stacks a SECOND row of controls under `ShellHeader`, which is
 * the only page that does. The header carries no `border-b` by design, so the
 * two rows were touching with nothing between them at all.
 */
describe("the work panel's tab strip is a layer below the header, not part of it", () => {
  it("clears the header by 16px — twice the spacing inside either row", () => {
    expect(workPanel).toContain("flex-col gap-0 bg-background pt-4");
  });

  it("puts the clearance on the panel, so Work-alone and Split cannot disagree", () => {
    // The split mounts this same component inside a `ResizablePanel`. Anything
    // placed in `index.tsx` instead would have to be written twice.
    expect(header).not.toMatch(/\bpb-\d/);
    expect(header).not.toMatch(/\bmb-\d/);
  });
});

describe("a workspace row is taller, and its title now outranks its entity line by SIZE", () => {
  it("grew its own padding for a more comfortable row", () => {
    expect(fleetTree).toContain('"h-auto w-full justify-start py-2 font-normal"');
    expect(fleetTree).not.toContain("py-1.5");
  });

  it("the title reads at the ramp's default size, not the metadata floor", () => {
    expect(fleetTree).toContain('text-sm text-content-primary');
  });

  it("the entity line stays at the metadata floor, so the title is visibly bigger", () => {
    expect(fleetTree).toContain(
      'className="text-content-tertiary flex w-full min-w-0 items-center gap-2 text-xs"',
    );
  });
});

describe("a project name keeps 8 characters before a branch beside it may crowd it out", () => {
  it("the rail row and the fleet card both apply the shared floor to the project only", () => {
    const card = readFileSync("components/grove/fleet/workspace-card.tsx", "utf8");
    expect(fleetTree).toMatch(/<ProjectLabel name=\{row\.repoName\} className=\{PROJECT_MIN_WIDTH\} \/>/);
    expect(card).toMatch(/<ProjectLabel name=\{repoName\} className=\{PROJECT_MIN_WIDTH\} \/>/);
    // Neither site puts the floor on the branch — it is the one side of the
    // pair that is meant to keep shrinking once the project hits its own.
    expect(fleetTree).not.toMatch(/<BranchLabel name=\{state\.branch\} className=/);
    expect(card).not.toMatch(/<BranchLabel name=\{state\.branch\} className=/);
  });
});
