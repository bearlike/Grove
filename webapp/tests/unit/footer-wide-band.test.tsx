import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { StatusFooter } from "@/components/grove/shell/status-footer";
import { TooltipProvider } from "@/components/ui/tooltip";
import {
  EMPTY_COUNTS,
  EMPTY_PROGRESS,
  fleetAttention,
  fleetProgress,
  hasGitActivity,
  type FooterContext,
} from "@/lib/grove/adapters/footer";
import type { DashboardSnapshotView } from "@/lib/grove/api";

/**
 * The WIDE band's redistribution (#816).
 *
 * Measured before this change: the band's content was 740px at 1280, 1600 AND
 * 2560 — it never grew, so 71% of a wide viewport was empty while the rail
 * cards beside it already showed git facts the footer held and discarded.
 *
 * The aggregate's coverage is the load-bearing assertion here. An average over
 * reported claims alone is systematically optimistic, because a finished ticket
 * keeps its claim while an untouched one has none — measured on this host, 96%
 * across claims against 60% across workspaces.
 */

const CONTEXT: FooterContext = {
  project: "Grove",
  subpath: null,
  branch: "main",
  worktree: null,
  runtime: "host",
  git: { ahead: 38, behind: 15, added: 7897, removed: 620, dirty: 2 },
};

function render(
  overrides: Partial<Parameters<typeof StatusFooter>[0]> = {},
): string {
  return renderToStaticMarkup(
    <TooltipProvider>
      <StatusFooter
        context={CONTEXT}
        counts={{ working: 1, idle: 0, blocked: 0 }}
        accounts={[]}
        system={{ version: "0.0.9", startedAt: "2026-09-19T08:00:00Z", updateAvailable: false, latestVersion: null, restartRequired: false }}
        connected
        progress={{ fraction: 0.6, reported: 8, tickets: 11 }}
        attention={2}
        {...overrides}
      />
    </TooltipProvider>,
  );
}

const text = (html: string): string => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();

describe("the git section states three separate questions", () => {
  it("renders ahead, behind, the branch delta and the dirty count", () => {
    const html = render();
    const git = html.slice(
      html.indexOf('data-testid="footer-git"'),
      html.indexOf('data-testid="footer-fleet"'),
    );
    expect(text(git)).toContain("38");
    expect(text(git)).toContain("15");
    expect(text(git)).toContain("+7.9K");
    expect(text(git)).toContain("−620");
    expect(text(git)).toContain("2 dirty");
  });

  it("gives added and removed OPPOSITE tones, never one 'changes' figure", () => {
    // Collapsing them would undo a split the engine keeps deliberately: a
    // branch delta and an uncommitted count answer different questions.
    const html = render();
    const git = html.slice(html.indexOf('data-testid="footer-git"'), html.indexOf('data-testid="footer-fleet"'));
    expect(git).toContain("text-success");
    expect(git).toContain("text-destructive");
  });

  it("renders NOTHING for an untouched workspace rather than a row of zeros", () => {
    const quiet = render({
      context: { ...CONTEXT, git: { ahead: 0, behind: 0, added: 0, removed: 0, dirty: 0 } },
    });
    expect(quiet).not.toContain('data-testid="footer-git"');
  });

  it("hasGitActivity is the one predicate, and a single nonzero is enough", () => {
    expect(hasGitActivity(null)).toBe(false);
    expect(hasGitActivity({ ahead: 0, behind: 0, added: 0, removed: 0, dirty: 0 })).toBe(false);
    expect(hasGitActivity({ ahead: 0, behind: 0, added: 0, removed: 0, dirty: 1 })).toBe(true);
    expect(hasGitActivity({ ahead: 1, behind: 0, added: 0, removed: 0, dirty: 0 })).toBe(true);
  });
});

describe("the fleet section says how the fleet is doing, once", () => {
  // Three sections used to answer this three overlapping ways and spent a
  // sentence on a denominator. One group of short figures now; the coverage
  // that keeps the percentage honest is a hover away rather than a headline.
  const fleet = (html: string): string =>
    html.slice(html.indexOf('data-testid="footer-fleet"'), html.indexOf('data-testid="footer-subscriptions"'));

  it("prints the ticket count and its mean progress as ONE short figure", () => {
    const section = fleet(render());
    expect(text(section)).toContain("11 tickets 60%");
    expect(text(section)).not.toContain("reported");
  });

  it("keeps the denominator one hover away — never dropped, never a headline", () => {
    // THE guard. 60% over 8 reported claims says something very different
    // from 60% over 11 attached tickets, and only one was measured.
    const tickets = /<span[^>]*data-testid="footer-fleet-tickets"[^>]*>/.exec(render())?.[0] ?? "";
    expect(tickets).toContain("8 of 11 tickets that reported");
  });

  it("prints the ticket count with NO percentage when nothing has reported", () => {
    const section = fleet(render({ progress: { fraction: null, reported: 0, tickets: 4 } }));
    expect(text(section)).toContain("4 tickets");
    expect(section).not.toMatch(/\d+%/);
  });

  it("shows attention only when it says MORE than blocked already did", () => {
    // Attention folds blocked in, so `1 blocked` beside `1 need you` is the
    // same workspace twice. Two waiting agents beside one blocked: 3 > 1.
    const both = fleet(render({ counts: { working: 0, idle: 0, blocked: 1 }, attention: 3 }));
    expect(text(both)).toContain("1 blocked");
    expect(text(both)).toContain("3 need you");
    const same = fleet(render({ counts: { working: 0, idle: 0, blocked: 2 }, attention: 2 }));
    expect(text(same)).toContain("2 blocked");
    expect(text(same)).not.toContain("need you");
  });

  it("divides its figures with WHITESPACE, never a seam", () => {
    // The figures are the parts of one answer; a rule between each was the
    // "too many borders" complaint in the neighbouring section.
    expect(fleet(render())).not.toContain('data-testid="footer-seam"');
  });

  it("names the runtime beside the PROJECT, not in the fleet aggregate", () => {
    const html = render();
    const workspace = html.slice(html.indexOf('data-testid="footer-workspace"'), html.indexOf('data-testid="footer-git"'));
    expect(text(workspace)).toContain("Host");
    expect(fleet(html)).not.toContain("Host");
  });

  it("renders no fleet section when there is nothing to report", () => {
    const bare = render({ counts: EMPTY_COUNTS, progress: EMPTY_PROGRESS, attention: 0 });
    expect(bare).not.toContain('data-testid="footer-fleet"');
  });
});

describe("the band spreads rather than stretching", () => {
  it("pins the system group right with the slack between, not inside a section", () => {
    // `ml-auto` puts the surplus BETWEEN the groups. Stretching each section
    // instead would re-centre every value whenever a neighbour changed width.
    //
    // MUTATION-TESTED: `expect(html).toContain("ml-auto")` SURVIVED deleting
    // this exact class, because the narrow summary's chevron carries one too
    // and both compositions are in the markup at once. The assertion has to
    // name the element that must carry it — a document-wide class search over
    // a two-layout band cannot say which layout it found.
    const html = render();
    const wrapper = html.lastIndexOf("<div", html.indexOf('data-testid="footer-uptime"'));
    expect(/class="([^"]*)"/.exec(html.slice(wrapper))?.[1]).toContain("ml-auto");
  });

  it("puts VERSION last, after uptime, each closed by its own rule", () => {
    // Asserted on the ORDER OF DISTINCT testids. Comparing two `indexOf`
    // results SURVIVED renaming uptime to `footer-system`: both lookups then
    // found the same element and `<` was trivially false rather than the
    // ordering being wrong. Reading the sequence out makes that unrepresentable.
    const order = [...render().matchAll(/<section [^>]*data-testid="(footer-[a-z]+)"/g)].map(
      (m) => m[1],
    );
    expect(order.at(-1)).toBe("footer-system");
    expect(order.at(-2)).toBe("footer-uptime");
    expect(order[0]).toBe("footer-workspace");
  });

  it("gives the two ENDS one shared accent, distinct from the middle wash", () => {
    const html = render();
    expect(html.match(/footer-accent/g) ?? []).toHaveLength(2);
    expect(html).toContain("footer-wash");
  });
});

describe("the fleet aggregate reads the snapshot the shell already holds", () => {
  const snapshot = {
    projects: [
      {
        repo_root: "/r",
        repo_name: "Grove",
        cwd: "/r",
        workspaces: [
          {
            state: { id: "a", ticket_refs: [{ provider: "gitea", id: "1" }, { provider: "gitea", id: "2" }] },
            needs_attention: true,
            phase: { phase: "deliver", index: 4, total: 6, tickets: [{ ticket: "gitea:1", phase: "deliver", index: 4 }] },
            sessions: [],
          },
          {
            state: { id: "b", ticket_refs: [{ provider: "gitea", id: "3" }] },
            needs_attention: false,
            phase: { phase: "handoff", index: 5, total: 6, tickets: [{ ticket: "gitea:3", phase: "handoff", index: 5 }] },
            sessions: [],
          },
        ],
      },
    ],
  } as unknown as DashboardSnapshotView;

  it("counts every attached ticket, not only the ones carrying a claim", () => {
    // The optimism guard at the adapter: 3 attached, 2 reported.
    expect(fleetProgress(snapshot)).toMatchObject({ tickets: 3, reported: 2 });
  });

  it("scores done as exactly 1 and takes the mean over CLAIMS", () => {
    // `delivering` is index 4 of a 6-step ramp = 0.8; `done` is 1. Mean = 0.9.
    expect(fleetProgress(snapshot).fraction).toBeCloseTo(0.9, 5);
  });

  it("reports no fraction at all when nothing has claimed", () => {
    const silent = { projects: [{ ...snapshot.projects[0]!, workspaces: [] }] } as unknown as DashboardSnapshotView;
    expect(fleetProgress(silent)).toEqual({ fraction: null, reported: 0, tickets: 0 });
  });

  it("counts attention from the daemon's own fold, which is BROADER than blocked", () => {
    // `needs_attention` folds waiting|blocked|error; `FleetCounts.blocked` is
    // literally blocked. The band shows both because they differ.
    expect(fleetAttention(snapshot)).toBe(1);
  });

  it("answers an absent snapshot without inventing zeroes as measurements", () => {
    expect(fleetProgress(undefined)).toEqual(EMPTY_PROGRESS);
    expect(fleetAttention(undefined)).toBe(0);
  });
});

describe("a disconnected band publishes no aggregate", () => {
  it("says the fleet is unavailable rather than showing stale figures", () => {
    const down = render({ connected: false, progress: EMPTY_PROGRESS, attention: 0, counts: EMPTY_COUNTS });
    expect(text(down)).toContain("fleet unavailable");
    expect(down).not.toContain('data-testid="footer-fleet-tickets"');
  });
});

describe("a suppressed reading is not an absent one (#816)", () => {
  it("names the remedy in the band rather than rendering nothing", () => {
    // The regression this closes: the #815 provenance guard correctly withheld
    // a stale worker's cumulative counters, and every surface then looked
    // exactly like an agent that had never reported. One has a remedy.
    const html = render({
      context: { ...CONTEXT, contextWindow: null, contextUnavailable: "stale_native_worker" },
    });
    expect(html).toContain('data-testid="footer-context-stale"');
    expect(text(html)).toContain("context needs respawn");
    expect(html).toContain("text-warning");
  });

  it("renders NOTHING when the window is genuinely unmeasured", () => {
    const html = render({
      context: { ...CONTEXT, contextWindow: null, contextUnavailable: null },
    });
    expect(html).not.toContain('data-testid="footer-context-stale"');
    expect(text(html)).not.toContain("respawn");
  });

  it("prefers a real reading over the notice when both could apply", () => {
    const html = render({
      context: {
        ...CONTEXT,
        contextWindow: { size: 1_000_000, used: 128_450, used_fraction: 0.128 },
        contextUnavailable: "stale_native_worker",
      },
    });
    expect(html).toContain('data-testid="footer-context-window"');
    expect(html).not.toContain('data-testid="footer-context-stale"');
    expect(text(html)).toContain("128.45K / 1M");
  });
});
