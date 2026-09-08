import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const tree = readFileSync("components/grove/fleet/fleet-tree.tsx", "utf8");

/**
 * The rail row has client-only navigation and menu state, so this focused suite
 * pins its static structural contracts without pretending SSR can exercise them.
 */
describe("compact sidebar workspace cards", () => {
  it("renders a raised object card with a title band and a separate link", () => {
    const row = tree.slice(tree.indexOf("function WorkspaceRow("), tree.indexOf("function FleetSkeleton("));

    expect(row).toContain("<CardShell");
    expect(row).toContain("surface-header");
    expect(row).toContain("<Link");
    expect(row).toContain("href={`/w/${state.id}`}");
    expect(row).toContain("<TooltipIconButton");
    expect(row).toContain("Workspace options");
    expect(row).toContain("min-h-[28px]");
    expect(row).toContain("[@media(pointer:coarse)]:min-h-11");
    expect(row).not.toContain("justify-between");
  });

  it("keeps the header for identity only and reserves the sibling options target", () => {
    const row = tree.slice(tree.indexOf("function WorkspaceRow("), tree.indexOf("function FleetSkeleton("));
    const header = row.slice(row.indexOf("<header"), row.indexOf("</header>") + "</header>".length);

    expect(header).toContain("<AgentMark");
    expect(header).toContain("<LoopingText");
    expect(header).not.toContain("fleet-row-attention-mark");
    expect(header).not.toContain("fleet-row-phase-mark");
    expect(header).toContain("pr-10");
    expect(row).toContain('className="invisible absolute top-px right-1.5');
    expect(header).toContain('min-h-[28px]');
    expect(row).toContain('[@media(pointer:coarse)]:visible');
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

  it("splits context from the metric ledger and lets its zero triple disappear", () => {
    const metrics = readFileSync("components/grove/fleet/workspace-metrics.tsx", "utf8");

    expect(metrics).toContain("context?: React.ReactNode");
    expect(metrics).toContain('data-testid="rail-ledger"');
    expect(metrics).toContain("const noChanges = dirty === 0 && added === 0 && removed === 0");
    expect(metrics).not.toContain("justify-between");
    expect(metrics).not.toContain("h-4 w-full");
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

    expect(row).toContain('"group relative min-w-0 border has-[:focus-visible]:outline-2');
    expect(row).toContain("active ? ROW_SELECTED : cn(ROW_RESTING,");
    expect(row).toContain("focus-visible:outline-none");
    expect(row).not.toContain("opacity-80");
    expect(row).toContain('"opacity-95 hover:opacity-100 focus-within:opacity-100"');
    expect(row).toContain('className="flex min-w-0 flex-col gap-1 px-2.5 py-2 text-sm text-content-secondary"');
    expect(row).not.toContain('active ? "py-2"');
  });

  it("keeps the skeleton in the card's compact two-band shape", () => {
    const skeleton = tree.slice(tree.indexOf("function FleetSkeleton("));

    expect(skeleton).toContain("<CardShell");
    expect(skeleton).toContain("surface-header");
    expect(skeleton).toContain("h-5 w-2/3");
    expect(skeleton).toContain("h-4 w-3/4");
  });
});
