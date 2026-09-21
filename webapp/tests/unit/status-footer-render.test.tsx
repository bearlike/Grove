import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AccountList } from "@/components/grove/shell/status-footer-accounts";
import { StatusFooter } from "@/components/grove/shell/status-footer";
import { TooltipProvider } from "@/components/ui/tooltip";
import {
  accountSummaries,
  fleetCounts,
  systemFacts,
  workspaceContext,
  EMPTY_COUNTS,
  EMPTY_PROGRESS,
} from "@/lib/grove/adapters/footer";
import type {
  DashboardSnapshotView,
  UsageQuotasView,
  WhoamiView,
} from "@/lib/grove/api";

/**
 * What the band RENDERS, from real adapter output (#814).
 *
 * A source census cannot see a section that failed to render, an icon that
 * went missing, or a colour that came from the wrong vocabulary — the lesson
 * `webapp/CLAUDE.md` records as "a green source census plus a green typecheck
 * is not a rendered control". `renderToStaticMarkup` needs no DOM, so this
 * stays in the node environment beside the pure adapter tests.
 *
 * TWO COUNTING TRAPS are encoded here, because both produced a false failure
 * against correct code while this was being written. A label string appears
 * TWICE per account (once in `title=`, once as text), so labels double-count —
 * count the gauge glyph instead. And a CLOSED Radix popover mounts no content
 * at all, so a static render shows the strip alone; the popover's completeness
 * is the adapter's question (`fit.all`) and a browser's, never this file's.
 */

const snapshot = {
  generated_at: "2026-09-19T05:00:00Z",
  total_workspaces: 3,
  needs_attention: 2,
  projects: [
    {
      repo_root: "/home/u/Agents",
      repo_name: "Agents",
      cwd: "/home/u/Agents/PA",
      workspaces: [
        {
          state: {
            id: "w1",
            repo_root: "/home/u/Agents",
            branch: "main",
            worktree_path: "/home/u/Agents/.worktrees/footer-ui",
          },
          // The LIVE branch, deliberately different from `state.branch`.
          branch: "feat/footer",
          sessions: [{ activity: { state: "working" } }],
        },
        {
          state: { id: "w2", repo_root: "/home/u/Agents", branch: "main", worktree_path: "/home/u/Agents" },
          sessions: [{ activity: { state: "blocked" } }],
        },
        {
          state: { id: "w3", repo_root: "/home/u/Agents", branch: "main", worktree_path: "/home/u/Agents" },
          sessions: [{ activity: { state: "idle" } }],
        },
      ],
    },
  ],
} as unknown as DashboardSnapshotView;

const quotas = {
  accounts: [
    // Labels are real-shaped: the daemon publishes the ACCOUNT identity here,
    // which on this host is an email address. The strip must not print it.
    {
      account_id: "a", provider: "claude_code", label: "someone@example.com",
      billing_mode: "subscription", status: "ok",
      subscription: { plan: "max", label: "max", detail: "20x" },
      windows: [
        { scope: "session", label: "5h", used_percent: 0 },
        { scope: "weekly", label: "7d", used_percent: 100 },
      ],
    },
    {
      account_id: "b", provider: "claude_code", label: "other@example.com",
      billing_mode: "subscription", status: "ok",
      subscription: { plan: "max", label: "max", detail: "5x" },
      windows: [{ scope: "weekly", label: "7d", used_percent: 7 }],
    },
    {
      account_id: "c", provider: "codex", label: "someone@example.com",
      billing_mode: "subscription", status: "ok",
      subscription: { plan: "pro", label: "pro", detail: null },
      windows: [{ scope: "weekly", label: "7d", used_percent: 4 }],
    },
    {
      account_id: "d", provider: "generic", label: "Alibaba token plan",
      billing_mode: "subscription", status: "stale",
      windows: [{ scope: "weekly", label: "7d", used_percent: 24 }],
    },
  ],
  coverage: {},
} as unknown as UsageQuotasView;

const whoami = {
  version: "0.0.9",
  started_at: "2026-09-18T08:06:58Z",
  latest_version: "0.0.9",
  update_available: false,
} as unknown as WhoamiView;

const context = workspaceContext(snapshot, "w1")!;
const accounts = accountSummaries(quotas);

function render(node: React.ReactElement): string {
  return renderToStaticMarkup(<TooltipProvider>{node}</TooltipProvider>);
}

const live = render(
  <StatusFooter
    context={context}
    counts={fleetCounts(snapshot)}
    accounts={accounts}
    system={systemFacts(whoami)}
    connected
    progress={EMPTY_PROGRESS}
    attention={0}
  />,
);

/**
 * The WIDE composition's own markup (#815).
 *
 * The band renders both layouts and hides one by breakpoint, so a
 * document-wide census can no longer say which layout it measured. Anything
 * asserting the desktop strip's content slices here first; anything about the
 * narrow summary names its own testid.
 */
const wideStrip = live.slice(live.indexOf('data-testid="footer-workspace"'));

describe("the band renders four separated sections", () => {
  it.each([
    "status-footer",
    "footer-workspace",
    "footer-fleet",
    "footer-subscriptions",
    "footer-system",
  ])("renders %s", (testId) => {
    expect(live).toContain(`data-testid="${testId}"`);
  });

  it("draws a rule on every section, the last one suppressed", () => {
    // ONE rule per rendered section, plus the band's own top rule closing it
    // against the page. Asserted as a RELATIONSHIP rather than a literal: the
    // count was 5 when the band had four sections and is 6 now that #816 added
    // git and progress, and a hard number would have to be re-guessed on every
    // such change while saying nothing about the invariant. `last:border-r-0`
    // removes the trailing vertical one.
    // Counted over the WHOLE band, not `wideStrip`: that slice begins after
    // the first section's own opening tag, so counting there is short by one.
    // And it counts SECTION elements rather than every `footer-*` testid —
    // `footer-context-window` is a value inside one.
    const sections = (live.match(/<section [^>]*data-testid="footer-/g) ?? []).length;
    expect(live.match(/footer-rule/g) ?? []).toHaveLength(sections + 1);
    expect(live).toContain("last:border-r-0");
    expect(live).toContain("border-t");
  });

  it("uses a rendered-pixel band height, not a rem utility", () => {
    // MEASURED ON THE DEPLOYED PAGE: `h-6` is 1.5rem, which renders 19.19px
    // at this app's 80% density root — not the approved 24px. A chrome band
    // is a physical contract; the type inside it stays on the rem ramp.
    // Scope to the ROOT element's own class list: `h-6` is legitimate
    // elsewhere in the tree, so a document-wide search proves nothing.
    const rootClass = /<footer class="([^"]*)"/.exec(live)?.[1] ?? "";
    expect(rootClass).toContain("status-footer");
    expect(rootClass.split(/\s+/)).not.toContain("h-6");
  });

  it("washes the two MIDDLE sections so the outer two read as a pair", () => {
    expect(live.match(/footer-wash/g) ?? []).toHaveLength(2);
  });

  it("shows EXACTLY ONE of its two compositions at any width (#815)", () => {
    // Both layouts are in the markup and the breakpoint picks one, so every
    // other assertion in this file is blind to which one a reader sees — a
    // static render cannot evaluate a media query. MUTATION-TESTED: dropping
    // `md:hidden` from the narrow wrapper, and dropping `hidden`/`md:flex`
    // from the wide one, BOTH left this whole file green until this existed.
    // Either mutation ships two stacked footers, which is the one failure the
    // rest of the suite structurally cannot see.
    const narrow = /class="([^"]*)"[^>]*>(?=<[^>]*data-testid="footer-summary-trigger")/.exec(live);
    expect(narrow?.[1]).toContain("lg:hidden");

    const wideOpen = live.lastIndexOf("<div", live.indexOf('data-testid="footer-workspace"'));
    const wideClass = /class="([^"]*)"/.exec(live.slice(wideOpen))?.[1] ?? "";
    expect(wideClass.split(/\s+/)).toContain("hidden");
    expect(wideClass).toContain("lg:flex");
  });

  it("appears only at the width where its own labels do (#815)", () => {
    // FOUND ON A DEPLOYED 820px TABLET, not in review: the strip switched on at
    // `md` while every label inside it waited for `lg`, so 768-1023px rendered
    // `Grove main 1 0 1 …` — the reported defect, at a width nobody had looked
    // at. A layout may only be shown where it is legible, so the container's
    // threshold and the labels' threshold are ONE threshold.
    //
    // The assertion is the absence of an inner gate BELOW the container's own
    // threshold: any `sm:`/`md:` inside this strip is either dead (it can never
    // be false below `lg`) or a second threshold, and the second one is the bug.
    //
    // `xl:` is a different thing and is deliberately NOT banned here. A gate
    // ABOVE `lg` can genuinely be both true and false while the strip is
    // visible, so it expresses "this band shows fewer values when it is
    // narrower" — which is #815's own rule, not a violation of it. What #815
    // forbids is a value losing its WORD; what this allows is the band losing
    // a whole value, with the remaining ones intact. The guard below pins that
    // distinction so the allowance cannot widen into the defect.
    expect(wideStrip).not.toMatch(/\b(sm|md):(inline|flex|inline-flex|block)/);
    expect(wideStrip).toContain("working");
    expect(wideStrip).toContain("idle");
    expect(wideStrip).toContain("blocked");
  });

});

describe("the band is ONE typeface at ONE size", () => {
  it("sets no `font-mono` anywhere in the wide strip", () => {
    // The branch and the worktree were the band's only mono, on the argument
    // that a ref is read character by character — true where you STOP to read
    // one, which is why they are still mono on a fleet card and in every
    // table. Chrome is the other case, and the rail already carries the same
    // exemption (design system §3). One word in a second typeface inside a
    // 24px strip reads as a defect rather than a category.
    expect(wideStrip).not.toContain("font-mono");
  });

  it("keeps `tabular-nums`, which was never the typeface's job", () => {
    // The two claims are separate: mono says a value IS a literal, tabular
    // figures say digits should line up. Dropping the face must not drop the
    // alignment, or a column of readings stops being comparable.
    expect(wideStrip).toContain("tabular-nums");
  });
});

describe("a reading in the band is a WHOLE percent", () => {
  it("prints no fractional percentage anywhere in the band", () => {
    // `23.6%` and `28.95%` were five and six characters of precision in the
    // band whose scarcest resource is width, and a moving tenth-of-a-percent
    // digit in a strip whose job is to stay still. Nobody acts on the
    // difference between 23.6% and 24% of a weekly quota.
    //
    // Scoped to the rendered TEXT, not the markup: a class name like
    // `max-w-[22ch]` contains digits and a `title` carries exact counts on
    // purpose. The fixture's generic account reports 23.63%, so this fails the
    // moment either formatter's decimal returns.
    const drawn = live.replace(/<[^>]+>/g, " ");
    expect(drawn).not.toMatch(/\d+\.\d+%/);
    // A percentage is still PRESENT — the assertion above passes trivially on a
    // band that prints no readings at all, which is the shape the fleet-tone
    // test in this file was mutation-bitten by.
    expect(drawn).toMatch(/\d+%/);
  });

  it("rounds for DISPLAY only, leaving the thresholds on the raw number", () => {
    // A rounded figure must never reach a comparison, or 99.6% would print
    // `100%` and claim a limit the provider never reported. The 100% account
    // is `destructive` and the 24% one is not, which is the ramp reading the
    // unrounded value it was given.
    const tones = [...live.matchAll(/data-testid="footer-account-percent"[^>]*data-tone="([a-z]+)"/g)];
    expect(tones.map((m) => m[1])).toContain("destructive");
  });
});

describe("the workspace section names what is actually checked out", () => {
  it("renders the LIVE branch, never the create-time snapshot", () => {
    // The whole point of the engine change: `state.branch` is `main` here.
    expect(live).toContain("feat/footer");
    expect(live).not.toContain(">main<");
  });

  it("renders the sub-path breadcrumb", () => {
    expect(live).toContain("PA");
  });

  it("renders the worktree's basename, not its whole path", () => {
    expect(live).toContain("footer-ui");
    expect(live).not.toContain(".worktrees/footer-ui");
  });
});

describe("the fleet section agrees with the rest of the app", () => {
  it("marks blocked with the same tone its fleet card uses", () => {
    // `AGENT_TONE.blocked` is destructive. A second opinion about severity is
    // how one surface under-reports what another calls urgent.
    //
    // MUTATION-TESTED: asserting `toContain("text-destructive")` over the whole
    // band SURVIVED reverting this to `text-warning`, because the exhausted
    // quota account carries that class too. The assertion has to scope to the
    // blocked figure's OWN element — a document-wide colour census cannot tell
    // which value wore the colour.
    const blocked = /<span[^>]*data-testid="footer-fleet-blocked"[^>]*>/.exec(live)?.[0] ?? "";
    expect(blocked).toContain("text-destructive");
    expect(blocked).not.toContain("text-warning");
    const fleet = live.slice(live.indexOf('data-testid="footer-fleet"'), live.indexOf('data-testid="footer-subscriptions"'));
    expect(fleet).toContain("lucide-octagon-alert");
  });

  it("DROPS a zero figure rather than greying it", () => {
    // A band is read at a glance and `0 idle` is a figure the eye has to
    // reject. The fleet section used to print all three counts whatever their
    // value; a fleet with nothing blocked now says nothing about blocking.
    const quiet = render(
      <StatusFooter
        context={context}
        counts={{ working: 2, idle: 0, blocked: 0 }}
        accounts={[]}
        system={null}
        connected
        progress={EMPTY_PROGRESS}
        attention={0}
      />,
    );
    expect(quiet).toContain('data-testid="footer-fleet-working"');
    expect(quiet).not.toContain('data-testid="footer-fleet-idle"');
    expect(quiet).not.toContain('data-testid="footer-fleet-blocked"');
  });

  it("renders NO fleet section at all when every figure is zero", () => {
    const empty = render(
      <StatusFooter
        context={context}
        counts={EMPTY_COUNTS}
        accounts={[]}
        system={null}
        connected
        progress={EMPTY_PROGRESS}
        attention={0}
      />,
    );
    expect(empty).not.toContain('data-testid="footer-fleet"');
  });
});

describe("the subscription strip sheds accounts before it squeezes the band", () => {
  /**
   * THIS FILE RENDERS THE NARROW-CAPACITY CASE, AND THAT IS THE POINT.
   *
   * The strip's capacity is now `useMinWidth(1280)`, which is false in a static
   * render exactly as it is on the server and on the first client paint — so
   * these assertions describe the 1024-1279px band, where the second account is
   * the value that gives way. Two accounts at `xl` and above is the adapter's
   * claim (`fitAccounts(six, MAX_INLINE_ACCOUNTS)` in `footer-adapter.test.ts`)
   * because a media query has no meaning without a viewport, and asserting it
   * here would be asserting the mock rather than the band.
   */
  it("shows ONE account below `xl`, never two squeezed ones", () => {
    // Count the ACCOUNT groups themselves: a label appears twice per account
    // (title + text) and the brand mark is shared with the root agent.
    //
    // SCOPED TO THE WIDE STRIP, because the band renders two compositions and
    // hides one by breakpoint (#815).
    expect(wideStrip.match(/data-testid="footer-account"/g) ?? []).toHaveLength(1);
  });

  it("keeps the shown account the FULLEST one, not the first configured", () => {
    // `max 20x` is at 100%; every other account is quiet. Shedding must not
    // drop the one reading a reader would act on — that is the whole reason
    // `accountSummaries` ranks by headroom.
    expect(wideStrip).toContain(">max 20x<");
    expect(wideStrip).not.toMatch(/>pro</);
    expect(wideStrip).not.toContain(">max 5x<");
    expect(wideStrip).not.toContain(">Alibaba token plan<");
  });

  it("moves every shed account into the overflow control, losing none", () => {
    expect(live).toContain('data-testid="footer-account-overflow"');
    // Three hidden at this capacity, where the wide band hides two.
    expect(live).toMatch(/\+3 accounts/);
  });

  it("prints the PLAN in the strip, keeping the account identity in the tooltip", () => {
    // The band is permanently visible, screenshares included, and an account
    // label is routinely an email address — the longest string here and the
    // one nobody chose to publish. Found on the deployed page, not in review.
    const strip = live.slice(
      live.indexOf('data-testid="footer-subscriptions"'),
      live.indexOf('data-testid="footer-account-overflow"'),
    );
    // The TIER, not the provider-prefixed plan: each account leads with its
    // provider's brand mark, so `Claude` beside the Claude logo would be the
    // one word said twice in a band measured in characters. The full plan
    // and the identity both remain in the tooltip.
    expect(strip).toContain(">max 20x<");
    expect(strip.match(/data-testid="footer-account"/g) ?? []).toHaveLength(1);
    // The identity stays REACHABLE as the hover/focus title — that is the
    // point, not an oversight — so strip the attributes before asserting the
    // email is not among the DRAWN text.
    const drawn = strip.replace(/\s(?:title|aria-label)="[^"]*"/g, "");
    expect(drawn).not.toContain("@");
    expect(strip).toContain('title="someone@example.com"');
  });

  it("BOUNDS every USER-SUPPLIED value in the band, so no label can take the row", () => {
    // THE DEFECT THIS FILE EXISTS TO CATCH NOW. Every value was `min-w-0
    // truncate`, which is a floor and not a ceiling: a 29-character plan drew
    // in full and took 133px while `main` was squeezed to 11px of the 24 it
    // needed.
    //
    // Scoped to what `Value` renders — the project, sub-path, branch, worktree,
    // agent and plan — because those are the strings a USER names and therefore
    // the only ones with no inherent length. The version (`v0.0.9`) and the
    // uptime are ours and bounded by construction; requiring a ceiling there
    // would be cargo-culting the fix onto values that cannot exhibit the bug.
    // `Value` is identifiable by the tooltip trigger it composes.
    const values = wideStrip.match(/class="min-w-0 truncate[^"]*"[^>]*data-slot="tooltip-trigger"/g) ?? [];
    // Six in this fixture; asserted as non-trivial rather than as a count, so
    // adding a section does not force a re-guess.
    expect(values.length).toBeGreaterThanOrEqual(4);
    for (const value of values) expect(value).toMatch(/max-w-\[\d+ch\]/);
  });

  it("CLIPS each section to itself, so none can paint over its neighbour", () => {
    // The last guard, and the only one a per-value ceiling cannot express. A
    // section also holds groups that legitimately cannot shrink — the context
    // reading, the runtime word — so a narrow band still sums past the
    // section's width. MEASURED AT 1024px on the built page: the workspace
    // section ended at 407px with its context figure drawn to 414px, through
    // its own closing rule and into the git section's first value. Every
    // section, because the one without it is the one that overflows.
    const sections = wideStrip.match(/<section [^>]*class="([^"]*)"[^>]*data-testid="footer-/g) ?? [];
    expect(sections.length).toBeGreaterThanOrEqual(3);
    for (const section of sections) expect(section).toContain("overflow-hidden");
  });

  it("MIRRORS a rigid group's own shrink onto its wrapper, so it cannot self-clip", () => {
    // A wrapper that is `min-w-0` around content that cannot elide shrinks
    // past its child; the child overflows, the section's `overflow-hidden`
    // cuts it, and it presents as uneven spacing around the divider rather
    // than as a clipped value. MEASURED at 1024px: the context reading sat
    // 4.9px past its wrapper with its `%` sliced mid-glyph, leaving 24.3px of
    // air before the section rule against 6.4px after it.
    //
    // The group already states this on its own root, so the wrapper reads it
    // rather than taking a prop: a rigid group gets a `shrink-0` wrapper, an
    // elastic one keeps `min-w-0` and therefore keeps truncating.
    // BOTH arms are asserted unconditionally. An `if (match)` here would be the
    // vacuous shape this suite has already been bitten by: a regex that stops
    // matching reports a pass, and "the wrapper is rigid" and "no wrapper was
    // found" are indistinguishable.
    // The wrapper is the LAST `Groups` wrapper opened before the target — that
    // wrapper's class list is unique to `Groups` (`items-center gap-1 px-1`),
    // so this finds it without assuming the target's own tag. An earlier
    // version walked back two `<span`s and was VACUOUS for the overflow
    // control, whose element is a `<button>`: it skipped past the wrapper onto
    // an unrelated span and passed while the bug was present. Mutation-tested.
    const WRAPPER = /<span class="(inline-flex items-center gap-1 px-1[^"]*)"/g;
    const wrapper = (childTestId: string): string => {
      const at = wideStrip.indexOf(`data-testid="${childTestId}"`);
      expect(at, `${childTestId} must render for this guard to mean anything`).toBeGreaterThan(-1);
      let found: string | null = null;
      WRAPPER.lastIndex = 0;
      for (let m = WRAPPER.exec(wideStrip); m !== null && m.index < at; m = WRAPPER.exec(wideStrip)) {
        found = m[1]!;
      }
      expect(found, `no Groups wrapper precedes ${childTestId}`).not.toBeNull();
      return found!;
    };

    // The rigid arm lives in `footer-context.test.tsx`, whose fixture has the
    // context window this one lacks — it is the group the original defect
    // clipped. Two probes were wrong before that landed and both are worth
    // naming: `FleetSection` hand-rolls its own wrapper and never calls
    // `Groups` at all, and `footer-account` holds an eliding plan, so it is
    // correctly ELASTIC. The presence assertion in `wrapper` is what turned
    // both into failures instead of silent passes.
    //
    // This file owns the elastic arm and the component case below.

    // The elastic case must NOT have been made rigid, or truncation dies with
    // the bug — this is the half the "drop min-w-0 everywhere" fix got wrong.
    const elastic = wrapper("footer-project");
    expect(elastic).toContain("min-w-0");
    expect(elastic.split(/\s+/)).not.toContain("shrink-0");

    // A GROUP RENDERED BY A COMPONENT MUST STATE ITS OWN RIGIDITY. The
    // detector reads the composed element's `className`, and a component's
    // internals are invisible there — `AccountOverflow`'s button is `shrink-0`
    // inside the component, so the group read as elastic, the wrapper took
    // `min-w-0`, and the clip cut `+3 accounts` by 7.5px at 1024px. Caught
    // only on the built page, after the first fix had already measured clean
    // for the context reading.
    const overflow = wrapper("footer-account-overflow");
    expect(overflow.split(/\s+/)).not.toContain("min-w-0");
  });

  it("bounds in `ch`, never in pixels, so the density root cannot re-scale it", () => {
    // `max-w-40` was a 160px bound written for a 16px root; this app renders at
    // an 80% root, so it silently meant something else. A `ch` ceiling is
    // stated in the band's own characters and tracks type, zoom and density.
    const subscriptions = wideStrip.slice(wideStrip.indexOf('data-testid="footer-subscriptions"'));
    expect(subscriptions).toMatch(/max-w-\[\d+ch\]/);
    expect(wideStrip).not.toContain("max-w-40");
  });
});

describe("it stays inside the app's own vocabulary", () => {
  it("hides every decorative glyph from the accessibility tree", () => {
    expect(live).not.toMatch(/<svg(?![^>]*aria-hidden)/);
  });

  it("names no raw palette class and no colour literal of its own", () => {
    expect(live).not.toMatch(/(bg|text|border)-(zinc|slate|gray|neutral|red|green|blue|amber)-\d{2,3}/);
    // Scoped to CLASS and STYLE attributes, which is what this guard always
    // meant. The band now leads each account and the root agent with the
    // vendored `AgentMark`, whose path data carries the brand's own fill —
    // the identity hue `agent-mark.tsx` documents as sanctioned under §4.1
    // (a fixed property of the thing, paid in imported SVG rather than a
    // class written here). A document-wide hex search cannot tell a brand
    // mark from a colour Grove chose; an attribute-scoped one can.
    expect(live).not.toMatch(/(class|style)="[^"]*#[0-9a-fA-F]{6}/);
    // And the marks are there, which is what makes the scoping load-bearing.
    expect(live).toContain('data-testid="agent-mark"');
  });
});

describe("a disconnected daemon says so rather than reporting zero", () => {
  const down = render(
    <StatusFooter
      context={context}
      counts={EMPTY_COUNTS}
      accounts={accounts}
      system={systemFacts(whoami)}
      connected={false}
      progress={EMPTY_PROGRESS}
      attention={0}
    />,
  );

  it("names what it cannot measure, in warning rather than destructive", () => {
    expect(down).toContain("fleet unavailable");
    expect(down).toContain("quota not measured");
    expect(down).toContain("text-warning");
  });

  it("publishes no counts at all", () => {
    // A stale cache rendered as a live count is the silently-wrong answer.
    expect(down).not.toMatch(/tabular-nums[^>]*>0</);
  });
});

describe("degraded and update states", () => {
  it("omits checkout facts when no workspace is open", () => {
    const bare = render(
      <StatusFooter
        context={{ project: "Agents", subpath: null, branch: null, worktree: null }}
        counts={fleetCounts(snapshot)}
        accounts={accounts}
        system={systemFacts(whoami)}
        connected
    progress={EMPTY_PROGRESS}
    attention={0}
      />,
    );
    expect(bare).toContain("Agents");
    expect(bare).not.toContain("feat/footer");
  });

  it("names the target version and links to the releases page", () => {
    const upd = render(
      <StatusFooter
        context={context}
        counts={EMPTY_COUNTS}
        accounts={[]}
        connected
    progress={EMPTY_PROGRESS}
    attention={0}
        system={systemFacts({ ...whoami, latest_version: "0.1.0", update_available: true })}
      />,
    );
    expect(upd).toContain("0.1.0");
    expect(upd).toContain("github.com/bearlike/Grove/releases");
  });
});

describe("the expanded account list names its providers by mark", () => {
  // The popover is the surface a reader opens to tell two accounts apart, and
  // both of this host's Claude accounts differ only by email. The mark is the
  // identity; a gauge tinted by headroom repeated the percentage beside it.
  // RENDERED DIRECTLY, not through `AccountOverflow`: Radix keeps its popover
  // content closed and portalled, so a static render of the overflow control
  // emits the trigger alone — the first version of this guard asserted against
  // an empty string and reported `[]` instead of a wrong brand.
  const popover = renderToStaticMarkup(
    <TooltipProvider>
      <AccountList accounts={accountSummaries(quotas)} />
    </TooltipProvider>,
  );

  it("renders one brand mark per account, in the daemon's own order", () => {
    const brands = [...popover.matchAll(/data-testid="agent-mark"[^>]*data-brand="(\w+)"/g)].map((m) => m[1]);
    // The fourth account's provider is `generic`, which resolves to the
    // neutral terminal mark rather than a brand — drawing one would be a
    // worse lie than no logo.
    // Fullest first (100, 24, 7, 4), so the brands follow that ranking rather
    // than the daemon's configured order.
    expect(brands).toEqual(["claude", "generic", "claude", "codex"]);
  });

  it("draws no generic gauge glyph beside a named provider", () => {
    expect(popover).not.toContain("lucide-gauge");
  });
});
