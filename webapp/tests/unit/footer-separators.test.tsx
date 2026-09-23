import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { StatusFooter } from "@/components/grove/shell/status-footer";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { AccountSummary, FooterContext } from "@/lib/grove/adapters/footer";

/**
 * The band's TWO tiers of division.
 *
 * Reported symptom: the footer ran project, branch, worktree, agent and context
 * together as one undivided stream, and two subscription accounts abutted with
 * nothing between them — so the boundary between one account and the next was no
 * stronger than the boundary between a plan and its own percentage. Two levels
 * of structure shared one divider, which is to say none.
 *
 * So the assertions here are about RELATIVE structure, never about a class
 * appearing somewhere in the document. A band that renders two layouts at once
 * makes any document-wide class search unable to say which layout it found —
 * the trap `footer-wide-band.test.tsx` already documents for `ml-auto`.
 */

const CONTEXT: FooterContext = {
  project: "Grove",
  subpath: "webapp",
  branch: "feat/footer",
  worktree: "footer-work",
  rootAgent: "Claude Code (via Gateway)",
  contextWindow: { size: 1_000_000, used: 128_450, used_fraction: 0.12845 },
  contextUnavailable: null,
  runtime: "host",
  git: { ahead: 3, behind: 1, added: 120, removed: 8, dirty: 2 },
};

const ACCOUNTS: AccountSummary[] = [
  {
    accountId: "a",
    label: "someone@example.com",
    shortLabel: "Claude max 20x",
    provider: "claude_code",
    percent: 100,
    window: "7d",
    status: "ok",
    stale: false,
  },
  {
    accountId: "b",
    label: "other@example.com",
    shortLabel: "Codex pro",
    provider: "codex",
    percent: 8,
    window: "5h",
    status: "ok",
    stale: false,
  },
];

function render(overrides: Partial<Parameters<typeof StatusFooter>[0]> = {}): string {
  return renderToStaticMarkup(
    <TooltipProvider>
      <StatusFooter
        context={CONTEXT}
        counts={{ working: 2, idle: 1, blocked: 1 }}
        accounts={ACCOUNTS}
        system={{ version: "0.0.9", startedAt: "2026-09-19T08:00:00Z", updateAvailable: false, latestVersion: null, restartRequired: false }}
        connected
        progress={{ fraction: 0.6, reported: 8, tickets: 11 }}
        attention={2}
        {...overrides}
      />
    </TooltipProvider>,
  );
}

/** The markup between two testids — where a divider between them has to fall. */
function between(html: string, first: string, second: string): string {
  const start = html.indexOf(`data-testid="${first}"`);
  const end = html.indexOf(`data-testid="${second}"`);
  expect(start).toBeGreaterThan(-1);
  expect(end).toBeGreaterThan(start);
  return html.slice(start, end);
}

const hasSeam = (fragment: string): boolean => fragment.includes('data-testid="footer-seam"');

describe("every subject in the workspace section is divided from the next", () => {
  const html = render();

  it.each([
    ["footer-project", "footer-branch"],
    ["footer-branch", "footer-worktree"],
    ["footer-worktree", "footer-root-agent"],
    ["footer-root-agent", "footer-context-window"],
  ])("draws a seam between %s and %s", (first, second) => {
    expect(hasSeam(between(html, first, second))).toBe(true);
  });

  it("keeps the sub-path INSIDE the project rather than seaming it off", () => {
    // `Grove › webapp` is one location stated in two parts, and the chevron is
    // already the separator for that relationship. A seam would claim they were
    // peers, which is the failure this whole change is about pointed backwards.
    expect(hasSeam(between(html, "footer-project", "footer-branch"))).toBe(true);
    const project = between(html, "footer-project", "footer-branch");
    const chevron = project.indexOf("lucide-chevron-right");
    const seam = project.indexOf('data-testid="footer-seam"');
    expect(chevron).toBeGreaterThan(-1);
    expect(chevron).toBeLessThan(seam);
  });
});

describe("the git section divides its three questions and only those", () => {
  const html = render();

  it("seams the sync pair, the branch delta and the dirty count apart", () => {
    expect(hasSeam(between(html, "footer-git-sync", "footer-git-delta"))).toBe(true);
    expect(hasSeam(between(html, "footer-git-delta", "footer-git-dirty"))).toBe(true);
  });

  it("does NOT seam ahead from behind — one comparison, two directions", () => {
    const sync = between(html, "footer-git-sync", "footer-git-delta");
    const upArrow = sync.indexOf("lucide-arrow-up");
    const downArrow = sync.indexOf("lucide-arrow-down");
    expect(upArrow).toBeGreaterThan(-1);
    expect(downArrow).toBeGreaterThan(upArrow);
    expect(hasSeam(sync.slice(upArrow, downArrow))).toBe(false);
  });
});

describe("each subscription is ONE group anchored by its brand mark", () => {
  const html = render();
  const section = html.slice(html.indexOf('data-testid="footer-subscriptions"'), html.indexOf('data-testid="footer-uptime"'));

  it("renders one account group per account, each LEADING with its provider's mark", () => {
    const groups = [...section.matchAll(/data-testid="footer-account"[^>]*>(?:(?!data-testid="footer-account").)*?data-testid="agent-mark"/gs)];
    expect(groups).toHaveLength(2);
    expect(section).toMatch(/data-testid="footer-account"[^>]*><svg[^>]*data-testid="agent-mark"/);
  });

  it("puts ONE seam between two accounts and NONE inside either", () => {
    // The reported defect, and the failure of the first repair: `Claude max
    // 20x 7d 100% Codex pro 5h 8%` as one run was fixed by drawing five
    // verticals, which was reported as "too many borders". The account is the
    // group; a seam divides groups; whitespace divides a group's values.
    const [first, second] = [...section.matchAll(/data-testid="footer-account"/g)].map((m) => m.index!);
    const inside = section.slice(first, second);
    // Exactly one seam between the two accounts' openings.
    expect(inside.match(/data-testid="footer-seam"/g) ?? []).toHaveLength(1);
    // ...and it sits AFTER the first account's reading, not between its plan and its reading.
    expect(inside.indexOf("100%")).toBeLessThan(inside.indexOf('data-testid="footer-seam"'));
    // No account carries its own border either.
    expect(section).not.toMatch(/data-testid="footer-account"[^>]*class="[^"]*border-l/);
    expect(section).not.toMatch(/class="[^"]*border-l[^"]*"[^>]*data-testid="footer-account"/);
  });

  it("drops the provider word the mark already says", () => {
    expect(section).toContain(">max 20x<");
    expect(section).not.toContain(">Claude max 20x<");
  });
});

describe("a seam never opens or closes a section", () => {
  it("draws no seam when only one group in a section renders", () => {
    // `Groups` places dividers BETWEEN rendered children rather than beside
    // each conditional one. Every group here is conditional, so a per-site
    // `{cond ? <Seam/> : null}` would leave a rule hanging off whichever
    // neighbour happened to be absent.
    const lone = render({
      context: {
        ...CONTEXT,
        subpath: null,
        branch: null,
        worktree: null,
        rootAgent: null,
        contextWindow: null,
        // Runtime lives in THIS section now (it moved out of the fleet
        // aggregate), so a fixture that leaves it set has two groups and a
        // seam between them is correct. Lone means lone.
        runtime: null,
        git: { ahead: 0, behind: 0, added: 0, removed: 0, dirty: 4 },
      },
    });
    const workspace = lone.slice(
      lone.indexOf('data-testid="footer-workspace"'),
      lone.indexOf('data-testid="footer-git"'),
    );
    expect(workspace).toContain("Grove");
    expect(hasSeam(workspace)).toBe(false);

    const git = lone.slice(
      lone.indexOf('data-testid="footer-git"'),
      lone.indexOf('data-testid="footer-fleet"'),
    );
    expect(git).toContain("dirty");
    expect(hasSeam(git)).toBe(false);
  });

  it("skips the absent group's seam rather than the present one's", () => {
    // Worktree absent: the seam count drops by exactly one and the remaining
    // dividers still sit between the groups that DID render.
    const rootPlaced = render({ context: { ...CONTEXT, worktree: null } });
    expect(hasSeam(between(rootPlaced, "footer-branch", "footer-root-agent"))).toBe(true);
    expect(rootPlaced).not.toContain('data-testid="footer-worktree"');
  });
});

describe("the two tiers are distinguishable, not merely both present", () => {
  /**
   * MUTATION-TESTED, AND THE FIRST VERSION OF THIS GUARD SURVIVED.
   *
   * It read the fragment starting at `class="footer-seam` and asserted the tag
   * did not also contain `footer-rule`. Repainting the seam with the loud
   * section token — which collapses the two tiers and is the one mutation that
   * undoes this entire change — left every one of the 13 tests green: the
   * anchor string no longer existed, `indexOf` returned -1, and the assertion
   * ran against an empty string. A search anchored on the very class under test
   * cannot fail when that class is what was removed.
   *
   * So the seam is located by its TESTID, which survives a repaint, and both
   * halves are asserted: it carries the quiet token and it does NOT carry the
   * loud one.
   */
  const SEAM_TAG = /<span[^>]*data-testid="footer-seam"[^>]*>/;

  it("draws every inner seam with the QUIET token", () => {
    const tag = SEAM_TAG.exec(render())?.[0];
    expect(tag).toBeDefined();
    expect(tag).toContain("footer-seam");
  });

  it("never paints an inner seam with the SECTION rule", () => {
    // One token for both would make every division equally loud, which carries
    // no grouping at all — the same failure as no dividers, pointed the other
    // way. This is the assertion the vacuous version could not make.
    const tag = SEAM_TAG.exec(render())?.[0];
    expect(tag).toBeDefined();
    expect(tag).not.toContain("footer-rule");
  });

  it("keeps the loud rule on the SECTIONS, which is the other half of the pair", () => {
    const sections = [...render().matchAll(/<section[^>]*data-testid="footer-[a-z]+"[^>]*>/g)];
    expect(sections.length).toBeGreaterThan(3);
    for (const [tag] of sections) expect(tag).toContain("footer-rule");
  });
});

describe("one size and one brand vocabulary for the whole band", () => {
  const html = render();
  const wide = html.slice(html.indexOf('data-testid="footer-workspace"'));

  it("names its size ONCE, on the footer, and nowhere inside the wide strip", () => {
    // MUTATION-TESTED: adding `text-sm` back onto `Figure` left every guard
    // green until this existed. Counted before the change: 18 `text-sm` and
    // 8 `text-xs` in one 24px strip. The root names the size; no descendant
    // may, or the band is back to two sizes per group. Scoped to the wide
    // strip because the phone sheet legitimately steps up one size.
    expect(/<footer class="[^"]*\btext-xs\b/.test(html)).toBe(true);
    expect(wide).not.toMatch(/class="[^"]*\btext-(sm|base|lg|xl|\[[^\]]+\])\b/);
    // `text-xs` may not be RE-STATED either — a descendant that says it is a
    // descendant someone will later change.
    expect(wide).not.toMatch(/class="[^"]*\btext-xs\b/);
  });

  it("leads the root agent and every account with the vendored brand mark", () => {
    // MUTATION-TESTED: replacing the root agent's mark with a lucide glyph
    // survived every guard. The mark is the identity every fleet row leads
    // with; the band must use the same one, not a generic bot.
    const agent = between(html, "footer-root-agent", "footer-context-window");
    expect(agent).toMatch(/^data-testid="footer-root-agent"[^>]*><svg[^>]*data-testid="agent-mark"[^>]*data-brand="claude"/);
    const accounts = [...wide.matchAll(/data-testid="footer-account"[^>]*><svg[^>]*data-testid="agent-mark"[^>]*data-brand="(\w+)"/g)].map((m) => m[1]);
    expect(accounts).toEqual(["claude", "codex"]);
  });
});

describe("the context percentage shares the Activity meter's ramp", () => {
  const at = (used: number, size: number): string => {
    const html = render({ context: { ...CONTEXT, contextWindow: { size, used, used_fraction: used / size } } });
    return /<span class="([^"]*)"[^>]*>\s*[\d.]+%/.exec(
      html.slice(html.indexOf('data-testid="footer-context-window"')),
    )?.[1] ?? "";
  };

  // Both sides of every boundary. The DISPLAYED percentage is rounded to two
  // decimals, and the ramp reads that same rounded value — so the step below
  // each threshold is the largest input that still rounds under it, not the
  // raw `threshold - 1` (499,999/1M rounds to exactly 50.00).
  it.each([
    [120_000, "text-info"],
    [499_949, "text-info"],
    [500_000, "text-success"],
    [799_949, "text-success"],
    [800_000, "text-warning"],
    [999_949, "text-warning"],
    [1_000_000, "text-destructive"],
    [1_400_000, "text-destructive"],
  ])("tones %s/1M as %s", (used, tone) => {
    expect(at(used, 1_000_000)).toContain(tone);
  });

  it("leaves the COUNTS quiet — only the percentage carries the tone", () => {
    // Toning both would make the whole group shout at 80%, in a band whose
    // job is to stay still.
    const html = render({ context: { ...CONTEXT, contextWindow: { size: 100, used: 95, used_fraction: 0.95 } } });
    const group = html.slice(html.indexOf('data-testid="footer-context-window"'));
    const counts = /<span class="([^"]*)"[^>]*>95 \/ 100</.exec(group)?.[1] ?? "";
    expect(counts).not.toMatch(/text-(info|success|warning|destructive)/);
  });
});

describe("the usage strip ranks by fullness and marks what is urgent", () => {
  const withAccounts = (percents: readonly (number | null)[]): string =>
    render({
      accounts: percents.map((percent, index) => ({
        accountId: `a${index}`,
        label: `user${index}@example.com`,
        shortLabel: `Claude tier ${index}`,
        provider: "claude_code" as const,
        percent,
        window: index % 2 === 0 ? "5h" : "7d",
        status: "ok" as const,
        stale: false,
      })),
    });

  const tones = (html: string): string[] =>
    [...html.matchAll(/data-testid="footer-account-percent" data-tone="(\w+)"/g)].map((m) => m[1]);

  it("tones ONLY the percentage, never the account row around it", () => {
    // A whole row changing colour at 50% makes a band whose job is to stay
    // still shout instead. The tone is on the value that moves.
    //
    // MUTATION-TESTED: asserting that the PLAN's own span carries no tone
    // class SURVIVED moving the tone onto the account wrapper — the plan
    // still had no class of its own and simply INHERITED the colour. Colour
    // inherits, so the guard has to name the ancestor that must not carry it.
    const html = withAccounts([95]);
    const wrapper = /<span[^>]*data-testid="footer-account"[^>]*>/.exec(html)?.[0] ?? "";
    expect(wrapper).not.toMatch(/text-(success|warning|destructive)/);
    expect(tones(html)).toEqual(["destructive"]);
  });

  it("pulses amber and above, and leaves a healthy reading still", () => {
    // MUTATION-SENSITIVE both ways: pulsing everything is as wrong as pulsing
    // nothing, because a permanently animated band is one people hide.
    const calm = withAccounts([12]);
    expect(calm).not.toContain("quota-urgent");
    const urgent = withAccounts([64]);
    expect(urgent).toContain("quota-urgent");
    const hot = withAccounts([99]);
    expect(hot).toContain("quota-urgent");
  });

  it("gives an unmeasured account no tone and no pulse", () => {
    const html = withAccounts([null]);
    expect(tones(html)).toEqual([]);
    expect(html).not.toContain("quota-urgent");
  });

  it("marks the overflow control as clickable with a dotted underline", () => {
    // It sits in a band of plain readings with a ghost variant that has no
    // resting fill, so `+2 accounts` otherwise looks exactly like `22
    // tickets` two sections away. Dotted, because solid is the app's link
    // affordance and this opens a popover rather than navigating.
    const html = withAccounts([90, 80, 70, 60]);
    const trigger = /<button[^>]*data-testid="footer-account-overflow"[^>]*>/.exec(html)?.[0] ?? "";
    expect(trigger).toContain("decoration-dotted");
    expect(trigger).toContain("underline");
  });
});
