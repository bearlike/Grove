import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  LoopingText,
  MarqueePauseButton,
  ScannedTextScope,
} from "@/components/grove/overflow-text";
import { BranchLabel, ProjectLabel } from "@/components/grove/entity";
import { SessionMetadata } from "@/components/grove/fleet/workspace-metrics";
import { workspace } from "@/tests/fixtures/fleet";

const metrics = readFileSync("components/grove/fleet/workspace-metrics.tsx", "utf8");
const overflow = readFileSync("components/grove/overflow-text.tsx", "utf8");

/**
 * What a STATIC render can and cannot say about a marquee, stated once so
 * nobody reads a green suite here as proof the thing moves.
 *
 * It CAN pin the hydration contract, the DOM the browser has to animate, the
 * accessible-name rule, the grid the geometry is solved on, and the absence of
 * monospace. It CANNOT pin motion — no measurement happens without layout, so
 * every assertion below sees the pre-measurement markup on purpose. A complete
 * animation cycle is measured in `tests/e2e/sidebar-sessions.spec.ts`, and that
 * spec is the only evidence the loop actually loops.
 */

describe("the server and the first client render agree, scope or no scope", () => {
  it("renders ONE copy and no separator before anything has been measured", () => {
    const bare = renderToStaticMarkup(<LoopingText>feat/sidebar-navigation</LoopingText>);
    const scoped = renderToStaticMarkup(
      <ScannedTextScope>
        <LoopingText>feat/sidebar-navigation</LoopingText>
      </ScannedTextScope>,
    );

    expect(bare).toBe(scoped);
    expect(scoped).toContain('data-looping="false"');
    expect(scoped).not.toContain("•");
    expect(scoped.match(/feat\/sidebar-navigation/g)).toHaveLength(1);
  });

  it("falls back to an ordinary clipped line, which is also the reduced-motion state", () => {
    const html = renderToStaticMarkup(<LoopingText>a very long label</LoopingText>);
    expect(html).toContain("truncate");
    expect(html).toContain("overflow-hidden");
  });

  it("hides every visual clone from the accessibility tree", () => {
    // The clone is client-only, so the RULE is pinned at its source: each
    // duplicate carries `aria-hidden`, and there is exactly one un-hidden copy.
    const clones = overflow.slice(overflow.indexOf("{looping ? ("));
    expect(clones).toContain("<span aria-hidden");
    expect(clones).toContain("<span ref={clone} aria-hidden");
  });
});

describe("the motion gates are the requirement, not a fallback", () => {
  it.each([
    ["a reader who asked the system for less motion", "prefers-reduced-motion: reduce"],
    ["a row scrolled out of the rail", "onScreen.has(box)"],
    ["a hidden document — NOT a blurred window", "document.hidden"],
    ["the shared pause control", "if (paused || !live) running.pause()"],
  ])("stops for %s", (_who, source) => {
    expect(overflow).toContain(source);
  });

  it("never gates on focus or hover, which would make the loop a hover affordance", () => {
    expect(overflow).not.toContain("window.addEventListener(\"blur\"");
    expect(overflow).not.toContain(":hover");
  });

  it("shares one observer pair across every label instead of one timer per row", () => {
    expect(overflow).toContain("let resizeObserver: ResizeObserver | undefined");
    expect(overflow).toContain("let intersectionObserver: IntersectionObserver | undefined");
    // The animation is the browser's, on `transform`, so nothing here ticks.
    expect(overflow).not.toContain("setInterval");
    expect(overflow).toContain("iterations: Infinity");
    expect(overflow).toContain('easing: "linear"');
  });

  it("wraps by exactly one text-plus-separator period, measured rather than assembled", () => {
    expect(overflow).toContain("second.offsetLeft - first.offsetLeft");
    expect(overflow).toContain('const SEPARATOR = " • "');
  });
});

describe("the pause control is discoverable and states the action", () => {
  it("names what pressing it will do and reports what it currently is", () => {
    const html = renderToStaticMarkup(
      <ScannedTextScope>
        <MarqueePauseButton />
      </ScannedTextScope>,
    );
    expect(html).toContain('aria-label="Pause scrolling text"');
    expect(html).toContain('aria-pressed="false"');
    expect(html).toContain('data-testid="marquee-pause"');
  });

  it("sits with the list's other instruments rather than in a settings surface", () => {
    const tree = readFileSync("components/grove/fleet/fleet-tree.tsx", "utf8");
    // The control ROW, delimited by its own class and the end of the branch
    // that renders it — a character distance would pass or fail on a reflow.
    const start = tree.indexOf('className="flex items-center gap-1.5"');
    const row = tree.slice(start, tree.indexOf(") : null}", start));
    expect(start).toBeGreaterThan(-1);
    expect(row).toContain("<NewWorkspaceButton");
    expect(row).not.toContain("<ThreadListSearch");
    expect(row).toContain("<FleetFilterMenu");
    expect(row).toMatch(/<MarqueePauseButton\b[^>]*\/>/);
  });
});

describe("the shared entity labels keep their behaviour outside the scope", () => {
  it("still announces the kind exactly once, looping or not", () => {
    const html = renderToStaticMarkup(
      <ScannedTextScope>
        <ProjectLabel name="Grove" />
      </ScannedTextScope>,
    );
    expect(html.match(/Project:/g)).toHaveLength(2); // the sr-only word + the wrapper title
    expect(html.match(/>Grove</g)).toHaveLength(1);
  });

  it("leaves the branch label monospace, because only the SIDEBAR dropped it", () => {
    expect(renderToStaticMarkup(<BranchLabel name="main" />)).toContain("font-mono");
  });
});

describe("the session metric ledger", () => {
  const html = renderToStaticMarkup(
    <SessionMetadata workspace={{ ...workspace({ id: "sample" }), dirty_files: 7, diff_added: 124, diff_removed: 18 }} />,
  );

  it("holds changes and creation age on a separate compact ledger line", () => {
    expect(html).toContain('data-testid="rail-ledger"');
    expect(html).toContain('data-testid="rail-dirty"');
    expect(html).toContain('data-testid="rail-added"');
    expect(html).toContain('data-testid="rail-removed"');
    expect(html).toContain('data-testid="rail-created"');
    expect(html).not.toContain("grid-cols-8");
  });

  it("keeps all ledger figures non-shrinking and outside the marquee", () => {
    expect(html.match(/flex shrink-0 items-center gap-1 whitespace-nowrap/g)).toHaveLength(4);
    const cells = html.slice(html.indexOf('data-testid="rail-dirty"'));
    expect(cells).not.toContain('data-testid="looping-text"');
    expect(metrics).toContain("<CreatedAge iso={workspace.state.created_at} />");
  });

  it("keeps the signs as text on the figure rather than a fifth icon cell", () => {
    expect(html).toContain("+124");
    expect(html).toContain("−18");
  });

  it("carries no monospace anywhere", () => {
    expect(html).not.toContain("font-mono");
  });

  it("says WHICH age it is showing, because the rail does not sort by it", () => {
    expect(html).toContain("Created: ");
    expect(metrics).toContain("workspace.state.created_at");
  });
});
