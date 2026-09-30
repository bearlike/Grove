import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const tree = readFileSync("components/grove/fleet/fleet-tree.tsx", "utf8");

/**
 * The rail row has client-only navigation and menu state, so this focused suite
 * pins its static structural contracts without pretending SSR can exercise them.
 */
describe("compact sidebar workspace cards", () => {
  it("renders a raised object card with a leading agent tile and a separate link", () => {
    const row = tree.slice(tree.indexOf("function WorkspaceRow("), tree.indexOf("function FleetSkeleton("));

    expect(row).toContain("<CardShell");
    // The tile is the row's identity column; its look is `.rail-agent-tile` at
    // the theme boundary, so no radius or colour utility enters this file.
    expect(row).toContain('className="rail-agent-tile flex size-10 shrink-0');
    expect(row).not.toContain("surface-header");
    expect(row).toContain("<Link");
    expect(row).toContain("href={`/w/${state.id}`}");
    expect(row).toContain("<TooltipIconButton");
    expect(row).toContain("Workspace options");
    expect(row).toContain("min-h-[28px]");
    expect(row).toContain("[@media(pointer:coarse)]:min-h-11");
    expect(row).not.toContain("justify-between");
  });

  it("keeps the title line for the name only and reserves the sibling options target", () => {
    const row = tree.slice(tree.indexOf("function WorkspaceRow("), tree.indexOf("function FleetSkeleton("));
    const header = row.slice(row.indexOf("<header"), row.indexOf("</header>") + "</header>".length);

    expect(header).not.toContain("<AgentMark");
    expect(header).toContain("<LoopingText");
    expect(header).not.toContain("fleet-row-attention-mark");
    expect(header).not.toContain("fleet-row-phase-mark");
    // The working mark left the title line (#876): the name gets the whole
    // width, so a long title loops through it instead of losing its tail.
    expect(header).not.toContain("<WorkingMark");
    expect(header).toContain("pr-9");
    expect(header).toContain('min-h-[28px]');
    // Options are visible at rest, not revealed on hover.
    expect(row).toContain('className="absolute top-2.5 right-1.5');
    expect(row).not.toContain("group-hover:visible");
    expect(row).not.toMatch(/className="invisible /);
  });

  it("puts the working mark LAST in the context row, after both claims", () => {
    const row = tree.slice(tree.indexOf("function WorkspaceRow("), tree.indexOf("function FleetSkeleton("));
    const context = row.slice(row.indexOf('data-testid="rail-context"'), row.indexOf("<TooltipContent"));
    const mark = context.indexOf("<WorkingMark");
    expect(mark).toBeGreaterThan(context.indexOf('data-testid="fleet-row-phase-mark"'));
    expect(context).toContain('agentState === "working" ? (');
  });

  it("puts natural-width branch, attention, and phase words in body context", () => {
    const row = tree.slice(tree.indexOf("function WorkspaceRow("), tree.indexOf("function FleetSkeleton("));

    const branch = row.indexOf('data-testid="rail-branch"');
    const attention = row.indexOf('data-testid="fleet-row-attention-mark"');
    const phase = row.indexOf('data-testid="fleet-row-phase-mark"');
    expect(row).toContain('data-testid="rail-context"');
    expect(branch).toBeGreaterThan(-1);
    expect(attention).toBeGreaterThan(branch);
    expect(phase).toBeGreaterThan(attention);
    expect(row).toContain("<span>{phaseText}</span>");
    expect(row).toContain('const phaseText = phase ? phaseLabel(phase.phase) : undefined');
    expect(row).toContain("const attentionText = row.workspace.needs_attention ? agentLabel(agentState) : undefined");
    expect(row).not.toContain("attentionMarkLabel.split");
    expect(row).not.toContain("STATUS_MARK_AREA");
  });

  it("divides context from the ledger and separates each figure with a rule", () => {
    const metrics = readFileSync("components/grove/fleet/workspace-metrics.tsx", "utf8");
    const body = metrics.slice(metrics.indexOf("export function SessionMetadata("), metrics.indexOf("export function SessionMetricDetails("));

    expect(body).toContain("context?: React.ReactNode");
    // The divider sits between the context and the ledger, in that order.
    const context = body.indexOf("{context}");
    const divider = body.indexOf('<Separator data-testid="rail-divider" />');
    const ledger = body.indexOf('data-testid="rail-ledger"');
    expect(context).toBeGreaterThan(-1);
    expect(divider).toBeGreaterThan(context);
    expect(ledger).toBeGreaterThan(divider);
    // Two short vertical rules, one between each pair of figures.
    expect(body.match(/<Separator orientation="vertical"/g)).toHaveLength(2);
    expect(body).not.toContain("noChanges");
    expect(body).not.toContain("justify-between");
    expect(body).not.toContain("h-4 w-full");
  });

  it("uses a row tooltip for keyboard focus while values retain individual descriptions", () => {
    const row = tree.slice(tree.indexOf("function WorkspaceRow("), tree.indexOf("function FleetSkeleton("));
    const metrics = readFileSync("components/grove/fleet/workspace-metrics.tsx", "utf8");

    expect(row).toContain("<TooltipTrigger asChild>");
    expect(row).toContain('<TooltipContent side="left" sideOffset={8}>');
    expect(row.match(/<TooltipTrigger asChild>/g)).toHaveLength(1);
    expect(row).toContain("<SessionMetricDetails workspace={row.workspace} />");
    expect(metrics).not.toContain("<TooltipTrigger");
    expect(metrics).toContain("aria-label={detail}");
  });

  it("keeps the actual card border on the card and never dims active text", () => {
    const row = tree.slice(tree.indexOf("function WorkspaceRow("), tree.indexOf("function FleetSkeleton("));

    expect(row).toContain('"rail-row group relative min-w-0 border has-[:focus-visible]:outline-2');
    expect(row).toContain("active ? ROW_SELECTED : ROW_RESTING,");
    expect(row).toContain("focus-visible:outline-none");
    expect(row).not.toContain("opacity-80");
    expect(row).not.toContain("opacity-95");
    expect(row).toContain('className="flex min-w-0 flex-1 flex-col gap-1.5 text-sm text-content-secondary"');
    expect(row).not.toContain('active ? "p-');
  });

  it("steps the fill by interaction state: rest darkest, hover between, selected full", () => {
    const row = tree.slice(tree.indexOf("function WorkspaceRow("), tree.indexOf("function FleetSkeleton("));
    const css = readFileSync("app/globals.css", "utf8");
    const ladder = css.slice(css.indexOf(".rail-row {"), css.indexOf("/* The landing configuration shelf"));

    expect(row).toContain('data-selected={active ? "true" : "false"}');
    // Selected is the base rule: the full card fill.
    expect(ladder).toMatch(/^\.rail-row \{\s*background-color: var\(--card\);\s*\}/);
    // Resting steps down half-way to the rail it lies on.
    expect(ladder).toMatch(
      /\.rail-row\[data-selected="false"\] \{\s*background-color: color-mix\(in oklab, var\(--card\) 50%, var\(--surface-sunken\)\);/,
    );
    // Hover AND keyboard focus take the midpoint, a preview of selection.
    expect(ladder).toMatch(
      /\.rail-row\[data-selected="false"\]:hover,\s*\.rail-row\[data-selected="false"\]:focus-within \{\s*background-color: color-mix\(in oklab, var\(--card\) 75%, var\(--surface-sunken\)\);/,
    );
    // The ladder keys on interaction only — never on what a row reports.
    expect(ladder).not.toMatch(/data-(attention|phase|status|state)=/);
  });

  it("steps the border with the fill, and never lets the link paint over it", () => {
    const states = tree.slice(tree.indexOf("const ROW_RESTING"), tree.indexOf("function WorkspaceRow("));

    // The link must not paint its own fill over the card's ladder.
    expect(states).not.toContain("hover:bg-");
    expect(states).toContain('const ROW_RESTING = "border-surface-edge hover:border-edge-control"');
    expect(states).toContain('const ROW_SELECTED = "border-edge-control"');
  });

  it("keeps the skeleton in the card's tile-and-three-lines shape", () => {
    const skeleton = tree.slice(tree.indexOf("function FleetSkeleton("));

    expect(skeleton).toContain("<CardShell");
    expect(skeleton).toContain("size-10 shrink-0");
    expect(skeleton).toContain("h-5 w-2/3");
    expect(skeleton).toContain("h-4 w-3/4");
  });

  it("wears the state claims as soft pills from the one token table", () => {
    const row = tree.slice(tree.indexOf("function WorkspaceRow("), tree.indexOf("function FleetSkeleton("));
    const tokens = readFileSync("components/grove/fleet/tokens.ts", "utf8");

    // Both marks are the canonical badge, toned by `railPillAccent`, never a
    // colour chosen at the call site (design system §6).
    const attention = row.slice(row.lastIndexOf("<Badge", row.indexOf('data-testid="fleet-row-attention-mark"')));
    expect(attention.slice(0, 200)).toContain('railPillAccent("attention")');
    expect(row).toContain('phase?.blocked ? "blocked" : phase?.phase === "handoff" ? "done" : "progress"');
    for (const id of ["fleet-row-attention-mark", "fleet-row-phase-mark"]) {
      const at = row.indexOf(`data-testid="${id}"`);
      expect(at).toBeGreaterThan(-1);
      const pill = row.slice(row.lastIndexOf("<Badge", at), at);
      expect(pill).not.toMatch(/text-destructive|text-warning|text-success/);
    }
    expect(tokens).toContain("const RAIL_PILL_ACCENT = {");
  });

  it("gives a handed-off row its edge, and lets selection outrank it", () => {
    const row = tree.slice(tree.indexOf("function WorkspaceRow("), tree.indexOf("function FleetSkeleton("));
    const selected = row.indexOf('data-testid="fleet-row-marker"');
    const done = row.indexOf('data-testid="fleet-row-done-edge"');
    expect(selected).toBeGreaterThan(-1);
    expect(done).toBeGreaterThan(selected);
    expect(row).toContain(') : phase?.phase === "handoff" && !phase.blocked ? (');
    // Both edges are one width, wide enough to read as the card's side.
    for (const at of [selected, done]) {
      expect(row.slice(at, at + 160)).toContain("absolute inset-y-0 left-0 w-1 ");
    }
  });
});
