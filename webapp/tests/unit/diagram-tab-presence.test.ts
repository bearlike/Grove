import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import {
  offeredPanelTabs,
  PANEL_TAB_VALUES,
  SHARED_TABS,
} from "@/components/grove/workspace/selectors";
import { diagramOf } from "@/lib/grove/api";

/**
 * The Diagram tab is the first CONDITIONAL member of a census whose other five
 * are unconditional, so the properties worth pinning are the ones that census
 * cannot express on its own: it appears only with a descriptor, it never
 * reaches the public surface, and it does not disturb the order.
 */
describe("offeredPanelTabs", () => {
  it("leaves an ordinary workspace's strip exactly as it was", () => {
    expect(offeredPanelTabs(true, false)).toEqual([
      "terminal",
      "changes",
      "files",
      "info",
      "controls",
    ]);
  });

  it("adds Diagram in the census's position, never appended", () => {
    expect(offeredPanelTabs(true, true)).toEqual([
      "terminal",
      "changes",
      "diagram",
      "files",
      "info",
      "controls",
    ]);
  });

  it("never offers Diagram to a public reader, descriptor or not", () => {
    // Chrome, not the boundary — the daemon's public routes carry no diagram at
    // all. Asserted anyway because a tab that renders is a tab that fetches.
    expect(offeredPanelTabs(false, true)).toEqual(SHARED_TABS);
    expect(offeredPanelTabs(false, true)).not.toContain("diagram");
  });

  it("offers only names that exist in the one census", () => {
    for (const value of offeredPanelTabs(true, true)) {
      expect(PANEL_TAB_VALUES).toContain(value);
    }
  });
});

describe("the Diagram tab is reachable without being restored", () => {
  it("is a real member of the census a live descriptor selects", () => {
    // Nothing restores a work tab any more (every visit lands on Info), so the
    // property that keeps a diagram reachable is that the agent opening one
    // selects this exact value — see `Workspace`'s announced-diagram effect.
    expect(PANEL_TAB_VALUES).toContain("diagram");
    expect(offeredPanelTabs(true, true)).toContain("diagram");
  });
});

/**
 * `WorkspaceStateView` is generated and its generated copy predates the
 * descriptor, so this narrowing is what every surface reads instead of
 * asserting a shape. It must treat a daemon that has not shipped the field —
 * and one that sends `null` — as "no diagram" rather than throwing.
 */
describe("diagramOf", () => {
  it("reads a well-formed descriptor", () => {
    const descriptor = { path: "docs/flow.drawio", session_id: "a".repeat(32), mode: "active" };
    expect(diagramOf({ id: "ws", diagram: descriptor })).toEqual(descriptor);
  });

  it("reads every absent or malformed shape as no diagram", () => {
    expect(diagramOf(undefined)).toBeNull();
    expect(diagramOf(null)).toBeNull();
    expect(diagramOf({ id: "ws" })).toBeNull();
    expect(diagramOf({ diagram: null })).toBeNull();
    expect(diagramOf({ diagram: { path: "a.drawio" } })).toBeNull();
    expect(diagramOf({ diagram: { path: "a.drawio", session_id: "x", mode: "editing" } })).toBeNull();
  });
});

/**
 * The auto-select edge must be a LIVE open, never a query resolving.
 *
 * A source census because the guard is inline in a component that needs a query
 * client, a peek and a live workspace to render at all. What it pins is the one
 * distinction the code depends on: `peek.data?.state` reads "no diagram" while
 * the peek is still in flight, so recording that as the first observation turns
 * every reload of a workspace that already has a diagram into a fake open.
 */
describe("workspace tab sub-bars", () => {
  const TERMINAL = readFileSync(
    new URL("../../components/grove/workspace/terminal-tab.tsx", import.meta.url),
    "utf8",
  );
  const DIAGRAM = readFileSync(
    new URL("../../components/grove/workspace/diagram-tab.tsx", import.meta.url),
    "utf8",
  );

  /**
   * BAND GEOMETRY AND TYPE SIZE ARE SEPARATE DECISIONS, and this test used to
   * weld them: it pinned a `max(12px, …)` floor beside the 32px band as though
   * one implied the other. Measured on the built app, that floor made these
   * sub-bars 12px while the terminal output they label rendered 9.6px — the
   * chrome shouting over its own content. The band is still 32px; the type
   * follows the rem ramp so it scales with the density root, with a reader's
   * font size and with zoom, which a px floor cannot do.
   */
  it("keeps terminal and Diagram chrome at the shared band geometry", () => {
    for (const source of [TERMINAL, DIAGRAM]) {
      expect(source).toContain('h-[32px]');
      expect(source).toContain('border-b border-border');
    }
  });

  it("sizes that chrome from the ramp, never from a fixed pixel floor", () => {
    for (const source of [TERMINAL, DIAGRAM]) {
      expect(source).not.toContain("max(12px");
      expect(source).not.toMatch(/fontSize:/);
    }
  });
});

describe("the diagram auto-select edge", () => {
  const WORKSPACE = readFileSync(
    new URL("../../components/grove/workspace/index.tsx", import.meta.url),
    "utf8",
  );

  it("observes nothing until the peek has actually answered", () => {
    expect(WORKSPACE).not.toContain("diagramOf(peek.data?.state)");
    expect(WORKSPACE).toContain("if (diagramSessionId === undefined) return;");
  });
});
