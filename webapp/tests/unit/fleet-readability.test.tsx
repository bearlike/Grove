import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { WorkspaceCard } from "@/components/grove/fleet/workspace-card";
import { SessionMetadata, WorkspaceMetrics } from "@/components/grove/fleet/workspace-metrics";
import { workspaceStatusText } from "@/components/grove/fleet/workspace-status";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { PhaseView } from "@/lib/grove/api";
import { workspace } from "@/tests/fixtures/fleet";

const phase: PhaseView = {
  phase: "verifying", note: "Checking the built page at narrow widths.", blocked: false,
  index: 3, total: 6, updated_at: "2026-09-06T12:00:00Z", tickets: [],
};

const renderCard = (grouped = false): string => {
  const ws = workspace({ id: "test", state: "working" });
  ws.phase = phase;
  ws.sessions[0].activity.current_task = "This human prompt must never appear in the card.";
  return renderToStaticMarkup(<TooltipProvider><WorkspaceCard workspace={ws} repoName="Sample project" grouped={grouped} /></TooltipProvider>);
};

describe("the Grove status is a report, not a prompt", () => {
  it("renders the phase note and excludes the latest human prompt", () => {
    const html = renderCard();
    expect(html).toContain(phase.note);
    expect(html).toContain("Grove status");
    expect(html).not.toContain("This human prompt");
  });

  it("uses a phase word for a blank note, and never fabricates a phase", () => {
    expect(workspaceStatusText({ phase: { ...phase, note: "  " } })).toBe("Verifying");
    expect(workspaceStatusText({ phase: null })).toBeNull();
    expect(workspaceStatusText({})).toBeNull();
  });

  it("retains the blocked flag beside the note", () => {
    expect(workspaceStatusText({ phase: { ...phase, blocked: true } })).toBe(`Blocked — ${phase.note}`);
  });

  it("states an absent report even if there is a session", () => {
    const ws = workspace({ id: "no-report" });
    ws.sessions[0].activity.current_task = "A stale prompt";
    const html = renderToStaticMarkup(<TooltipProvider><WorkspaceCard workspace={ws} repoName="Sample" /></TooltipProvider>);
    expect(html).toContain("No Grove status reported yet.");
    expect(html).not.toContain("A stale prompt");
  });

  it("avoids repeating the project in grouped cards but keeps the branch", () => {
    expect(renderCard()).toContain('title="Project: Sample project"');
    expect(renderCard(true)).not.toContain('title="Project: Sample project"');
    expect(renderCard(true)).toContain("feat/test");
  });
});

describe("the same labelled change vocabulary reaches both surfaces", () => {
  const ws = { ...workspace({ id: "changed" }), diff_added: 12345, diff_removed: 25, dirty_files: 3, base_ahead: 7, base_behind: 2, queue: { pending: 4 } };

  it("shows signed deltas and current dirty paths in the rail", () => {
    const html = renderToStaticMarkup(<SessionMetadata workspace={ws} />);
    expect(html).toContain("+12.3K");
    expect(html).toContain("−25");
    expect(html).toContain("3 uncommitted files in the worktree");
    expect(html).toContain("12,345 lines added on the branch since its diff base");
    expect(html).toContain("text-success");
    expect(html).toContain("text-destructive");
    expect(html).toContain("text-warning");
  });

  it("labels branch divergence honestly instead of claiming pushes", () => {
    const html = renderToStaticMarkup(<WorkspaceMetrics workspace={ws} />);
    for (const label of ["added", "removed", "dirty files", "ahead", "behind", "queued"]) expect(html).toContain(`>${label}</dt>`);
    expect(html).toContain("7 commits ahead of the base branch (not unpushed commits)");
    expect(html).toContain("4 messages queued by the agent harness");
    expect(html).not.toContain(">pushes<");
  });

  it("omits the zero change triple while retaining creation age", () => {
    const html = renderToStaticMarkup(<SessionMetadata workspace={workspace({ id: "quiet" })} />);
    expect(html).not.toContain('data-testid="rail-dirty"');
    expect(html).not.toContain('data-testid="rail-added"');
    expect(html).not.toContain('data-testid="rail-removed"');
    expect(html).toContain('data-testid="rail-ledger"');
    expect(html).toContain('data-testid="rail-created"');
  });

  it("keeps zero dirty paths when the branch has a real diff", () => {
    const changed = { ...workspace({ id: "diff" }), dirty_files: 0, diff_added: 1, diff_removed: 0 };
    const html = renderToStaticMarkup(<SessionMetadata workspace={changed} />);
    expect(html).toContain('data-testid="rail-dirty"');
    expect(html).toContain('data-testid="rail-added"');
    expect(html).toContain('data-testid="rail-removed"');
    expect(html).toContain("+1");
  });

  // The daemon types all three counters as required numbers today, so this
  // degrades the payload on purpose. It pins the PRESENTATION rule — unknown is
  // a dash, zero is a zero — against the day one of them becomes nullable,
  // which is the only moment the difference would otherwise show up as a
  // fabricated clean branch.
  it("says UNKNOWN with a dash and never with a zero", () => {
    const unknown = { ...workspace({ id: "unmeasured" }), dirty_files: null as unknown as number };
    const html = renderToStaticMarkup(<SessionMetadata workspace={unknown} />);
    expect(html).toContain("—");
    expect(html).toContain("An unknown number of uncommitted files in the worktree");
    expect(html).not.toContain("0 uncommitted files");
  });

  it("does not invent a queue count when the snapshot has no queue", () => {
    const html = renderToStaticMarkup(<WorkspaceMetrics workspace={workspace({ id: "quiet" })} />);
    expect(html).not.toContain(">queued</dt>");
  });
});
