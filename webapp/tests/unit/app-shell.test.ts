import { existsSync, readFileSync } from "node:fs";

import { code } from "./_source";
import { describe, expect, it } from "vitest";

import { RAIL_WIDTH } from "@/components/grove/shell/rail-width";

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

describe("the desktop rail shares one readable measure", () => {
  it("uses 23.8rem, approximately 305px at the desktop density root", () => {
    // The measure moved OUT of this file when a second rail appeared (the
    // public share view), so the width is now asserted at its single source
    // and the shell is only checked for using it. Asserting the old literal
    // here would pin the spelling of a call site rather than the measure.
    expect(RAIL_WIDTH).toBe("w-[23.8rem]");
    expect(shell).toContain('collapsed ? "w-12" : RAIL_WIDTH');
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
    // IMPORTED rather than regexed out of the shell, so this assertion cannot
    // go stale the next time the rail is widened — which is exactly the drift
    // that produced #57 in the first place. It used to derive the value from
    // `app-shell.tsx`'s source text; now that the measure has its own module
    // the test reads the same export the components do, which is strictly
    // better: the previous form silently passed `undefined` into the
    // assertions below the moment the call site's spelling changed.
    expect(fleetTree).not.toContain(`"${RAIL_WIDTH}`);
    expect(fleetTree).not.toContain("RAIL_WIDTH");
    expect(fleetTree).not.toMatch(/collapsed \? "w-12/);
  });

  it("is w-full in both the collapsed and expanded states", () => {
    // Asserted as the two class strings rather than one concatenated
    // expression: the formatter is free to wrap the ternary across lines, and
    // the contract is what each state renders, not where the line breaks.
    expect(fleetTree).toContain('"w-full overflow-hidden px-2 pt-1 [@media(pointer:coarse)]:px-0.5"');
    expect(fleetTree).toContain(': "w-full overflow-y-auto p-3"');
  });
});

describe("the rail and workspace header are independently sized chrome", () => {
  it("shares the header band without a top-only shell inset", () => {
    expect(shell).toContain("px-2 pb-2 md:pl-0");
    expect(sidebar).toContain('"workspace-header flex shrink-0 items-center gap-2 [@media(pointer:coarse)]:h-14"');
    expect(sidebar).toContain('<BrandMark className="size-7 shrink-0" />');
  });

  it("fits native 24px controls inside the 32px workspace header", () => {
    expect(header).toContain('workspace-header');
    expect(readFileSync("app/globals.css", "utf8")).toMatch(/\.workspace-header\s*\{\s*min-height: 32px/);
    expect(header).not.toContain("h-12");
    expect(header).toContain("size-6 min-h-[24px] min-w-[24px]");
    expect(header).toContain("[@media(pointer:coarse)]:h-14");
  });

  it("the header line still carries the toggle, the title and nothing portalled in", () => {
    expect(header).toContain('data-testid="shell-sidebar-toggle"');
    expect(header).toMatch(/\{title \?\? sectionFor\(pathname\)\.label\}/);
  });
});

describe("workspace search is an explicit dialog, not a persistent rail field", () => {
  it("keeps the native search input accessible inside the one shell-owned palette", () => {
    const palette = readFileSync("components/grove/fleet/fleet-palette.tsx", "utf8");
    expect(palette).toContain('aria-label="Search workspaces"');
    expect(palette).toContain('placeholder="Search workspaces"');
    expect(palette).toContain("shouldFilter={false}");
  });

  it("leaves the rail action row free of a persistent search input", () => {
    expect(fleetTree).not.toContain("ThreadListSearch");
    expect(fleetTree).toContain("FleetFilterMenu");
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
  it("expanded, bands share a horizontal gutter while the scroller clears the edge fades", () => {
    expect(fleetTree).toContain('"w-full overflow-y-auto p-3"');
    expect(sidebar).toContain('collapsed ? "px-2 [@media(pointer:coarse)]:px-0.5" : "border-b border-border px-3"');
    expect(sidebar).toContain('collapsed ? "p-2 [@media(pointer:coarse)]:px-0.5" : "p-3"');
  });

  it("collapsed, every band stays px-2 — 8 + a 32px icon + 8 IS the 48px rail", () => {
    // A wider gutter here does not look roomier, it pushes the icons off
    // centre, so the collapsed state is deliberately excluded from all of it.
    // Asserted as the two class strings rather than one concatenated
    // expression: the formatter is free to wrap the ternary across lines, and
    // the contract is what each state renders, not where the line breaks.
    expect(fleetTree).toContain('"w-full overflow-hidden px-2 pt-1 [@media(pointer:coarse)]:px-0.5"');
    expect(shell).toContain('"w-12"');
  });
});

describe("roomier vertical spacing preserves compact horizontal controls", () => {
  // Every element that separates two ROWS, named individually rather than
  // counted — symmetry is the entire request, so one survivor at the old 2px
  // is the defect, and a count would be satisfied by any four of the five.
  it.each([
    ["the list itself", "relative flex flex-1 flex-col gap-3 transition-[padding]"],
    ["the collapse-fade group", "flex shrink-0 flex-col gap-3 transition-opacity"],
    ["the search and filter row", 'className="flex items-center gap-1.5"'],
    ["the loading skeleton, which is shaped like the rows", 'className="flex flex-col gap-3"'],
  ])("gives %s its specified spacing", (_name, className) => {
    expect(fleetTree).toContain(className);
  });

  it("the footer's destinations run on the same rhythm as the list above them", () => {
    expect(sidebar).toContain("flex shrink-0 flex-col gap-1.5 border-t");
  });

  it("keeps body lines closer than neighboring workspace cards", () => {
    const metrics = code("components/grove/fleet/workspace-metrics.tsx");
    expect(metrics).toMatch(/flex min-w-0 flex-col gap-1.*data-testid="rail-metadata"/);
    expect(fleetTree).toContain('className="flex flex-col gap-3"');
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

/** Three adjacent 32px bands read through their closing rules, not gaps. */
describe("workspace chrome has a fixed three-band budget", () => {
  it("closes the shell header at its 32px band edge", () => {
    expect(header).toContain('workspace-header');
    expect(readFileSync("app/globals.css", "utf8")).toMatch(/\.workspace-header\s*\{\s*min-height: 32px/);
    expect(header).toContain('border-b border-border');
    expect(header).not.toContain('h-8');
  });

  /**
   * `size` PICKS THE TYPE and the explicit height picks the band — the vendored
   * variant welds them, and the caller has always overridden the height. `sm`
   * therefore bought only the smaller type, which measured 9.6px on the built
   * app: metadata size on the app's primary navigation.
   */
  it("uses the vendor's nav type inside an explicit 32px work band", () => {
    expect(workPanel).toContain('AdaptiveTabsList');
    expect(workPanel).toContain('workspace-work-tabs');
    expect(readFileSync("app/globals.css", "utf8")).toMatch(/\.workspace-work-tabs\s*\{\s*min-height: 32px/);
    expect(workPanel).not.toContain('"pt-4"');
    expect(workPanel).not.toContain('activeTab === "diagram" ? "pt-0" : "pt-4"');
  });

  it("keeps native pane navigation at the rendered type floor with no inter-band gap", () => {
    const workspace = readFileSync("components/grove/workspace/index.tsx", "utf8");
    const adaptive = readFileSync("components/grove/workspace/adaptive-tabs.tsx", "utf8");
    expect(workspace).toContain('AdaptiveTabsList className="h-full"');
    expect(adaptive).toContain('variant="line"');
    expect(adaptive).toContain('@/components/ui/tabs');
    expect(workspace).not.toContain('@/components/assistant-ui/tabs');
    expect(workspace).not.toContain('mt-[8px]');
  });

  /**
   * THE HIT AREA IS PINNED IN GEOMETRY, THE TYPE IS LEFT TO THE RAMP.
   *
   * Every one of these bands carried a `max(12px, …)` inline floor, and
   * measured on the built app that floor was the defect rather than the
   * safeguard: the chrome held 12px while the content it frames rendered
   * 9.6-11.2px, so the frame outweighed the picture on every surface at once.
   * `min-h-[24px]` above is what keeps the pointer and finger targets, which is
   * a separate decision from how large the label reads (design-system §1).
   */
  it("sizes all three bands from the rem ramp, never a fixed pixel floor", () => {
    const workspace = readFileSync("components/grove/workspace/index.tsx", "utf8");
    const sources = {
      header: code("components/grove/shell/shell-header.tsx"),
      workPanel: code("components/grove/workspace/work-panel.tsx"),
      workspace: code("components/grove/workspace/index.tsx"),
    };
    for (const [name, source] of Object.entries(sources)) {
      expect(source, name).not.toContain("max(12px");
      expect(source, name).not.toMatch(/fontSize:/);
    }
  });
});


describe("the shell keeps its structural edges", () => {
  it("separates the rail with a border instead of a second width", () => {
    expect(shell).toContain('border-r border-border');
  });

  it("keeps the split separator full-height on its existing border rule", () => {
    // ONE divider for every split in the app: the workspace's and the
    // annotation pane's both compose `SplitHandle`, and that atom is where the
    // full-height rule lives. A second `ResizableHandle` call site is how two
    // splits come to draw two different dividers.
    const handle = readFileSync("components/grove/split-handle.tsx", "utf8");
    expect(handle).toContain('"h-full transition-colors after:w-3');
    for (const file of ["components/grove/workspace/index.tsx", "components/grove/annotation/annotation-host.tsx"]) {
      const source = readFileSync(file, "utf8");
      expect(source, file).toContain("<SplitHandle");
      expect(source, file).not.toContain("<ResizableHandle");
    }
  });
});

/**
 * THE HEADER REPORTS NOTHING ANY MORE — an ABSENCE, so it is pinned as a source
 * census: there is no runtime artifact to assert against once a component is
 * gone, and a deleted file plus a surviving import is exactly the shape that
 * would still typecheck against a stale build.
 *
 * The pill was a scrolling sentence of the agent's own prose, sitting one gap
 * from the pane tabs in a 32px band whose job is to stay still. The claim did
 * not disappear with it: the same `PhaseView` note renders on the Task card,
 * where a reader who came to read a status can read one that is not moving.
 */
describe("the workspace header navigates and no longer reports", () => {
  it("mounts no status pill and keeps no space for one", () => {
    const workspace = readFileSync("components/grove/workspace/index.tsx", "utf8");
    expect(existsSync("components/grove/workspace/status-pill.tsx")).toBe(false);
    expect(workspace).not.toContain("StatusPill");
    expect(workspace).not.toContain("status-pill");
    expect(workspace).not.toContain("agentStatusProps");
    // No reserved column, spacer or replacement mark stood in for it.
    expect(workspace).not.toContain('data-slot="agent-status"');
  });

  it("leaves the sidebar's looping text and the progress APIs alone", () => {
    const overflow = readFileSync("components/grove/overflow-text.tsx", "utf8");
    expect(overflow).toContain("LoopingText");
    expect(overflow).toContain("useOverflowMotion");
    // The adapter the pill consumed is still the fleet's, and still exported.
    expect(readFileSync("lib/grove/adapters/status.ts", "utf8")).toContain(
      "export function agentStatusProps",
    );
  });
});

/** Browser tests measure height and adjacency; these pin the composition boundary. */
describe("session navigation composes the shared card material", () => {
  it("uses CardShell without importing or modifying the vendored Card", () => {
    expect(fleetTree).toContain('from "@/components/grove/card"');
    expect(fleetTree).not.toContain('from "@/components/ui/card"');
    expect(fleetTree).not.toContain('h-17 w-full');
  });

  it("keeps the title in the material band and independent states in the body", () => {
    const row = code("components/grove/fleet/fleet-tree.tsx").split("function WorkspaceRow")[1];
    const header = row.match(/<header\b[\s\S]*?<\/header>/)?.[0];
    expect(header).toBeDefined();
    expect(header).toContain("surface-header");
    expect(header).toContain("<LoopingText");
    expect(header).toContain("text-base");
    expect(header).not.toContain('data-testid="fleet-row-attention-mark"');
    expect(header).not.toContain('data-testid="fleet-row-phase-mark"');
    expect(row).toContain('data-testid="rail-context"');
  });

  it("uses the same card header and body for the loading skeleton", () => {
    const skeleton = fleetTree.split("function FleetSkeleton")[1];
    expect(skeleton).toContain("<CardShell");
    expect(skeleton).toContain("surface-header");
    expect(skeleton).not.toContain("h-17");
  });

  it("keeps options outside the navigation link with an actual pointer floor", () => {
    const row = code("components/grove/fleet/fleet-tree.tsx").split("function WorkspaceRow")[1];
    expect(row.indexOf("</Link>")).toBeLessThan(row.indexOf("<DropdownMenuTrigger"));
    expect(row).toContain("min-h-[28px]");
    expect(row).toContain("min-w-[28px]");
    expect(row).not.toContain("opacity-80");
  });
});

describe("the sidebar keeps its scanned-text and project identity contracts", () => {
  it("does not compose the mono branch label, and no rail file asks for font-mono", () => {
    const metrics = readFileSync("components/grove/fleet/workspace-metrics.tsx", "utf8");
    for (const source of [fleetTree, sidebar, metrics]) {
      expect(source).not.toContain("font-mono");
    }
    expect(fleetTree).not.toContain("BranchLabel");
    // The branch keeps its ONE glyph even though the geometry forbids the
    // label's own anatomy — the mark is imported, never re-chosen.
    expect(fleetTree).toContain("BRANCH_GLYPH");
  });

  it("keeps a flat row's project name in the accessible description rather than a third line", () => {
    expect(fleetTree).toContain('<span className="sr-only">Project: {row.repoName}</span>');
    expect(fleetTree).not.toContain("PROJECT_MIN_WIDTH");
  });

  it("the fleet card still applies the project floor — this was a RAIL change only", () => {
    const card = readFileSync("components/grove/fleet/workspace-card.tsx", "utf8");
    expect(card).toMatch(/<ProjectLabel\s+name=\{repoName\}\s+className=\{PROJECT_MIN_WIDTH\}\s*\/>/);
    expect(card).not.toMatch(/<BranchLabel name=\{state\.branch\} className=/);
  });
});
