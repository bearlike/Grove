import { readFileSync } from "node:fs";

import { renderToStaticMarkup } from "react-dom/server";

import { TooltipProvider } from "@/components/ui/tooltip";
import { describe, expect, it } from "vitest";

import { FleetBoard } from "@/components/grove/fleet/fleet-board";
import { FleetProgressStrip } from "@/components/grove/fleet/fleet-progress";
import {
  bulkTargets,
  COLUMN_KEYS,
  columnOf,
  fleetProgress,
  invertSelection,
  kanbanColumns,
  NO_SELECTION,
  pruneSelection,
  selectAll,
  toggleSelected,
  workingCount,
} from "@/components/grove/fleet/kanban";
import type { FleetRow, TaskPhase, WorkspaceStatus } from "@/components/grove/fleet/types";
import type { PhaseView } from "@/lib/grove/api";
import { workspace } from "@/tests/fixtures/fleet";
import { code } from "./_source";

/**
 * THE BOARD IS A READ OF THE AGENTS' CLAIMS, NEVER A PLACE TO MAKE ONE.
 *
 * Every assertion below pins one of three facts: a column is exactly the
 * phase a workspace reported (no claim → "Unreported", never scope); the
 * figures over the board can only understate (blocked handoff is not done);
 * and selection is over the VISIBLE rows, so the filter is the bulk
 * instrument and a hidden row can never be acted on.
 */

function claim(phase: TaskPhase, blocked = false): PhaseView {
  const index = ["scope", "plan", "build", "verify", "deliver", "handoff"].indexOf(phase);
  return { phase, index, total: 6, note: null, blocked, updated_at: "2026-10-18T00:00:00Z", tickets: [] };
}

function row(
  id: string,
  over: { phase?: PhaseView; status?: WorkspaceStatus; attention?: boolean; state?: "working" | "idle" } = {},
): FleetRow {
  const base = workspace({ id, needsAttention: over.attention, state: over.state });
  return {
    workspace: {
      ...base,
      phase: over.phase,
      state: { ...base.state, status: over.status ?? "active" },
    },
    repoName: "grove",
    repoRoot: "/repos/grove",
  };
}

describe("kanbanColumns", () => {
  it("places a row in exactly the column its claim names, and the unclaimed in Unreported", () => {
    const rows = [row("a", { phase: claim("build") }), row("b"), row("c", { phase: claim("handoff") })];
    expect(columnOf(rows[0]!)).toBe("build");
    expect(columnOf(rows[1]!)).toBe("unreported");
    const columns = kanbanColumns(rows);
    expect(columns.map((c) => c.key)).toEqual([...COLUMN_KEYS]);
    expect(columns.find((c) => c.key === "build")?.rows.map((r) => r.workspace.state.id)).toEqual(["a"]);
    expect(columns.find((c) => c.key === "unreported")?.rows.map((r) => r.workspace.state.id)).toEqual(["b"]);
    expect(columns.find((c) => c.key === "scope")?.rows).toEqual([]);
  });

  it("keeps every column even when the board is empty", () => {
    expect(kanbanColumns([]).length).toBe(COLUMN_KEYS.length);
  });
});

describe("fleetProgress", () => {
  it("counts a blocked handoff as blocked, never as done", () => {
    const rows = [
      row("done", { phase: claim("handoff") }),
      row("stuck", { phase: claim("handoff", true) }),
      row("mid", { phase: claim("verify") }),
      row("quiet"),
      row("asks", { phase: claim("build"), attention: true }),
    ];
    const progress = fleetProgress(rows);
    expect(progress.total).toBe(5);
    expect(progress.done).toBe(1);
    expect(progress.blocked).toBe(1);
    expect(progress.unreported).toBe(1);
    expect(progress.inFlight).toBe(3);
    expect(progress.attention).toBe(1);
    expect(progress.fraction).toBeCloseTo(0.2);
    expect(progress.byColumn.find((c) => c.key === "handoff")?.count).toBe(2);
  });

  it("reports no fraction for an empty board rather than 0%", () => {
    expect(fleetProgress([]).fraction).toBeNull();
  });
});

describe("selection", () => {
  const visible = [row("a"), row("b"), row("c")];

  it("select all, invert and toggle are over the visible rows", () => {
    const all = selectAll(visible);
    expect([...all]).toEqual(["a", "b", "c"]);
    const some = toggleSelected(NO_SELECTION, "b");
    expect([...invertSelection(visible, some)]).toEqual(["a", "c"]);
    expect(toggleSelected(some, "b").size).toBe(0);
  });

  it("prunes ids that the filter hid, and returns the same set when nothing changed", () => {
    const held = selectAll(visible);
    expect(pruneSelection(held, visible)).toBe(held);
    expect([...pruneSelection(held, visible.slice(1))]).toEqual(["b", "c"]);
  });

  it("offers each verb only to the rows that can take it", () => {
    const mixed = [
      row("live", { status: "active", state: "working" }),
      row("napping", { status: "paused" }),
      row("gone", { status: "offline" }),
    ];
    const targets = bulkTargets(mixed, selectAll(mixed));
    expect(targets.pause).toEqual(["live"]);
    expect(targets.resume).toEqual(["napping"]);
    expect(targets.kill).toEqual(["live", "napping", "gone"]);
    expect(workingCount(mixed, selectAll(mixed))).toBe(1);
  });
});

describe("FleetBoard", () => {
  it("renders one column per key with its count, and a selected card marked", () => {
    const rows = [row("a", { phase: claim("plan") }), row("b")];
    const html = renderToStaticMarkup(
      <FleetBoard rows={rows} selected={new Set(["a"])} onToggleSelected={() => undefined} grouped={false} />,
    );
    const columns = html.match(/data-testid="fleet-board-column"/g) ?? [];
    expect(columns.length).toBe(COLUMN_KEYS.length);
    expect(html).toContain('data-column="plan" data-count="1"');
    expect(html).toContain('data-column="unreported" data-count="1"');
    expect(html).toContain('data-selected="true"');
    expect(html).toContain('aria-label="Select a"');
    // The rail's overview rides every card as a tooltip TRIGGER: a closed
    // Radix tooltip renders no content, so the trigger is the only honest SSR
    // witness, and the card's own badge tooltips make the slot count useless.
    expect((html.match(/data-testid="fleet-board-overview-trigger"/g) ?? []).length).toBe(2);
  });

  it("folds empty columns to a full-height rail wearing the count badge; open ones are equal vertical stacks", () => {
    const rows = [row("a", { phase: claim("build") }), row("b", { phase: claim("build") }), row("c")];
    const html = renderToStaticMarkup(
      <FleetBoard rows={rows} selected={NO_SELECTION} onToggleSelected={() => undefined} grouped={false} />,
    );
    expect((html.match(/data-collapsed="true"/g) ?? []).length).toBe(COLUMN_KEYS.length - 2);
    expect((html.match(/data-testid="fleet-board-expand"/g) ?? []).length).toBe(COLUMN_KEYS.length - 2);
    expect(html).toContain('data-column="build" data-count="2" data-collapsed="false"');
    // Never count-weighted and never tiled: every open lane is an equal
    // `flex-1 basis-0` vertical stack, and every lane — folded too — is `h-full`.
    expect(html).not.toContain("flex-grow:");
    expect(html).not.toContain("grid-cols-[repeat(auto-fill");
    expect((html.match(/fleet-column h-full/g) ?? []).length).toBe(COLUMN_KEYS.length);
    // A folded rail carries the same count badge the open header does.
    expect((html.match(/data-testid="fleet-board-count"/g) ?? []).length).toBe(COLUMN_KEYS.length);
    expect(html).toContain('data-blocked="false"');
    // The switch off: every column open, none foldable.
    const open = renderToStaticMarkup(
      <FleetBoard rows={rows} selected={NO_SELECTION} onToggleSelected={() => undefined} grouped={false} hideEmpty={false} />,
    );
    expect(open).not.toContain('data-collapsed="true"');
    expect((open.match(/data-testid="fleet-board-empty-column"/g) ?? []).length).toBe(COLUMN_KEYS.length - 2);
  });

  it("marks a blocked card so the theme can outrank the column hue", () => {
    const html = renderToStaticMarkup(
      <FleetBoard rows={[row("x", { phase: claim("verify", true) })]} selected={NO_SELECTION} onToggleSelected={() => undefined} grouped={false} />,
    );
    expect(html).toContain('data-blocked="true"');
  });

  it("keys every column's hue on one custom property in the theme", () => {
    const css = readFileSync("app/globals.css", "utf8");
    for (const key of COLUMN_KEYS) expect(css).toContain(`[data-column="${key}"] { --column-hue:`);
    expect(css).toContain(".fleet-board-card[data-blocked=\"true\"]");
    expect(css).toContain(".fleet-phase-bar [data-segment] { background-color: var(--column-hue); }");
  });

  it("never wires drag and drop — a card is a link, not a handle", () => {
    const source = code("components/grove/fleet/fleet-board.tsx");
    expect(source).not.toMatch(/\bKanbanBoardCard\b/);
    expect(source).not.toMatch(/\bdraggable\b/);
    expect(source).not.toMatch(/onDropOver/);
  });
});

describe("FleetProgressStrip", () => {
  it("prints every figure with its word and a segment per non-empty column", () => {
    const html = renderToStaticMarkup(
      <TooltipProvider>
        <FleetProgressStrip
          progress={fleetProgress([row("x", { phase: claim("handoff") }), row("y", { phase: claim("build") }), row("z")])}
        />
      </TooltipProvider>,
    );
    expect(html).toContain("Complete");
    expect(html).toContain(">33%<");
    expect(html).toContain("In flight");
    expect(html).toContain("Blocked");
    expect(html).toContain("Needs attention");
    expect((html.match(/data-segment="/g) ?? []).length).toBe(3);
    expect(html).toContain('aria-label="Unreported 1, Build 1, Handoff 1"');
  });
});

describe("the rail collapses on /fleet without touching the preference", () => {
  it("is wired once in the shell and never calls setCollapsed", () => {
    const state = code("components/grove/shell/sidebar-state.ts");
    const shell = code("components/grove/shell/app-shell.tsx");
    expect(state).toContain('"/fleet"');
    expect((shell.match(/useRouteCollapse\(/g) ?? []).length).toBe(1);
    const body = state.slice(state.indexOf("export function useRouteCollapse"));
    expect(body.slice(0, body.indexOf("\n}\n"))).not.toContain("setCollapsed");
    expect(body.slice(0, body.indexOf("\n}\n"))).not.toContain("remember(");
  });
});
