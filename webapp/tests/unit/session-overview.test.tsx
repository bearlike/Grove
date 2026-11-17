import { readFileSync } from "node:fs";
import { join } from "node:path";

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { duration } from "@/components/grove/duration";
import { SessionOverview } from "@/components/grove/fleet/session-overview";
import { rollupCoverage, ticketRollup } from "@/components/grove/workspace/selectors";
import type { PhaseView, TicketRef, WorkspaceActivityView } from "@/lib/grove/api";
import { formatContextWindow, formatTokenCount } from "@/lib/grove/adapters/context";
import { workspace } from "@/tests/fixtures/fleet";

/**
 * THE SIDEBAR TOOLTIP'S BODY: one bounded card, three sections, no chrome.
 *
 * It answers "what is this session doing and what does it hold" from a hover, so
 * the failures worth pinning are the ones that would make it LIE quietly:
 * reading a sub-agent's row as the workspace's, painting an absent measurement
 * as zero (or a real zero as absent), flattering the ticket rollup, and letting
 * a late PR fall off a three-ref preview. Everything is asserted on the
 * RENDERED MARKUP with no provider around it: the card sits inside a tooltip, so
 * it must not need (or open) a second one.
 *
 * Fixture values are chosen to be pairwise distinct and never substrings of one
 * another, so "the primary's figure is present" and "the child's is absent" can
 * each only be true for the reason the test names.
 */

const NOT_REPORTED = "Not reported";

type Session = WorkspaceActivityView["sessions"][number];
type SessionOver = {
  activity?: Partial<Session["activity"]>;
  duration?: Session["duration"];
  tokens?: Session["tokens"];
  latency?: Session["latency"];
};

function withSession(base: Session, over: SessionOver): Session {
  return {
    ...base,
    activity: { ...base.activity, ...over.activity },
    ...("duration" in over ? { duration: over.duration } : {}),
    ...("tokens" in over ? { tokens: over.tokens } : {}),
    ...("latency" in over ? { latency: over.latency } : {}),
  };
}

// `executionMs` (labour: sub-agent threads summed) feeds "Compute" and
// `activeMs` (the wall clock) feeds "Clock". `elapsedMs` is a birth-to-last
// -event span the card never shows, so it is a DECOY: distinct from the other
// clocks, and asserted ABSENT, which is what proves the card did not read it.
const PRIMARY = {
  model: "primary-opus-model",
  toolMs: 17_000,
  generationMs: 33 * 60_000,
  executionMs: 4 * 3_600_000 + 27 * 60_000,
  activeMs: 3 * 3_600_000 + 12 * 60_000,
  elapsedMs: 9 * 3_600_000 + 5 * 60_000,
  tokensIn: 12_345,
  tokensOut: 6_789,
  context: { size: 200_000, used: 64_000, used_fraction: 0.32 },
  cost: 1.2345,
};

const CHILD = {
  model: "child-haiku-model",
  toolMs: 58_000,
  generationMs: 21 * 60_000,
  executionMs: 8 * 3_600_000 + 13 * 60_000,
  activeMs: 5 * 3_600_000 + 40 * 60_000,
  elapsedMs: 6 * 3_600_000 + 6 * 60_000,
  tokensIn: 987_654,
  tokensOut: 543_210,
  context: { size: 200_000, used: 150_000, used_fraction: 0.75 },
  cost: 9.87,
};

function populated(
  base: Session,
  who: typeof PRIMARY,
  extra: Partial<Session["activity"]> = {},
): Session {
  return withSession(base, {
    activity: {
      model: who.model,
      tokens_in: who.tokensIn,
      tokens_out: who.tokensOut,
      context: who.context,
      native: { cost_usd: who.cost, permission_denials: 0 },
      ...extra,
    },
    duration: {
      tool_ms: who.toolMs,
      generation_ms: who.generationMs,
      execution_ms: who.executionMs,
      active_ms: who.activeMs,
      elapsed_span_ms: who.elapsedMs,
      confidence: "measured",
    },
  });
}

const ref = (id: string, over: Partial<TicketRef> = {}): TicketRef => ({
  provider: "gitea",
  id,
  kind: "issue",
  title: null,
  url: null,
  status: "open",
  draft: false,
  assignee: null,
  ambiguous: false,
  ...over,
});

const claim = (ticket: string, phase: PhaseView["phase"], index: number, blocked = false) =>
  ({ ticket, phase, index, blocked, note: null }) as PhaseView["tickets"][number];

const phaseView = (
  over: Partial<PhaseView> = {},
  tickets: PhaseView["tickets"] = [],
): PhaseView => ({
  phase: "build",
  note: null,
  blocked: false,
  updated_at: "2026-08-11T00:00:00Z",
  index: 2,
  total: 6,
  tickets,
  ...over,
});

/** A workspace whose primary AND a child are fully populated, with distinct figures. */
function fixture(over: Partial<WorkspaceActivityView> = {}): WorkspaceActivityView {
  const base = workspace({ id: "ws-private-id-7q", title: "Private title zz", state: "working" });
  const [first] = base.sessions;
  const child: Session = {
    ...populated(first, CHILD),
    session: { ...first.session, session_id: "session-child-id" },
  };
  return {
    ...base,
    state: {
      ...base.state,
      branch: "stale/state-branch",
      base_branch: "release-base",
      created_at: "2026-03-04T05:06:07Z",
      updated_at: "2026-03-05T05:06:07Z",
    },
    branch: "live/top-branch",
    sessions: [populated(first, PRIMARY), child],
    diff_added: 321,
    diff_removed: 654,
    dirty_files: 7,
    base_ahead: 4,
    base_behind: 9,
    recent_commits: [
      {
        sha: "abcdef1234567890abcdef1234567890abcdef12",
        subject: "A commit subject",
        committed_at: "2026-08-01T10:00:00Z",
      },
    ],
    phase: phaseView({ note: "rebasing onto main" }),
    ...over,
  };
}

function withRefs(
  ws: WorkspaceActivityView,
  refs: readonly TicketRef[],
  phase: PhaseView | null = ws.phase ?? null,
): WorkspaceActivityView {
  return { ...ws, state: { ...ws.state, ticket_refs: [...refs] }, phase };
}

/** NO provider: the card must stand alone inside a tooltip that already exists. */
const render = (ws: WorkspaceActivityView, unread?: boolean): string =>
  renderToStaticMarkup(<SessionOverview workspace={ws} unread={unread} />);

const textOf = (html: string): string =>
  html
    .replace(/<[^>]+>/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&#x27;|&#39;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/\s+/g, " ")
    .trim();

const codes = (html: string): string[] =>
  [...html.matchAll(/<code\b[^>]*>([\s\S]*?)<\/code>/g)].map((match) => textOf(match[1]));

const count = (haystack: string, needle: string): number => haystack.split(needle).length - 1;

/** One section's markup, so an absence is asserted where it is MEANT and not card-wide. */
function section(html: string, label: string): string {
  const match = html.match(new RegExp(`<section aria-label="${label}">([\\s\\S]*?)</section>`));
  expect(match, `section "${label}" must render`).not.toBeNull();
  return match![1];
}

const GLOBALS = readFileSync(join(__dirname, "../../app/globals.css"), "utf8");

/** A top-level rule's declarations, so layout is pinned in the stylesheet that owns it. */
function rule(selector: string): string {
  const escaped = selector.replace(/[.[\]]/g, "\\$&");
  // The selector may sit in a group (`.a, .b {`), but must be a whole selector,
  // not the prefix of a longer class name.
  const match = GLOBALS.match(new RegExp(`(?:^|\\n)[^\\n{}]*${escaped}(?![\\w-])[^\\n{}]*\\{([^}]*)\\}`));
  expect(match, `${selector} must have a rule in globals.css`).not.toBeNull();
  return match![1];
}

/** The value cell beside one label, so a figure is asserted against the row that owns it. */
function field(html: string, label: string): string {
  const match = html.match(new RegExp(`<dt>${label}</dt><dd>([\\s\\S]*?)</dd>`));
  expect(match, `field "${label}" must render`).not.toBeNull();
  return textOf(match![1]);
}

// Cost keeps up to four decimals (the Info tab's established precision), so a
// sub-cent figure is not rounded away, and a zero still reads as a zero.
const USD_EXACT = "$1.2345";
const USD_CHILD = "$9.87";
const ZERO_USD = /\$0\.00(?![\d])/;

/** The context row exactly as the Info tab's meter reads it: percent, then used of size. */
const contextText = (context: { used: number; size: number }): string => {
  const read = formatContextWindow(context.used, context.size)!;
  return `${read.percent}% · ${read.used} of ${read.size} tokens`;
};

/** Rows in the ticket preview, counted by the component's own testid. */
const ticketRows = (html: string): number => count(html, 'data-testid="session-overview-ticket"');

describe("the three sections", () => {
  // The groups are named for assistive tech; dividers, not headings, separate them visually.
  const labels = (html: string): string[] =>
    [...html.matchAll(/<section aria-label="([^"]+)">/g)].map((m) => m[1]);

  it("names exactly Session status, Attached tickets and Worktree, in that order", () => {
    const html = render(withRefs(fixture(), [ref("11")]));
    expect(labels(html)).toEqual(["Session status", "Attached tickets", "Worktree"]);
    const at = ["Session status", "Attached tickets", "Worktree"].map((name) => html.indexOf(`aria-label="${name}"`));
    expect(at.every((index) => index >= 0)).toBe(true);
    expect([...at].sort((a, b) => a - b)).toEqual(at);
  });

  it("still names every section for a bare workspace, rather than collapsing one away", () => {
    expect(labels(render(workspace({ id: "bare", state: "idle" })))).toEqual([
      "Session status",
      "Attached tickets",
      "Worktree",
    ]);
  });
});

describe("what the card must NOT carry", () => {
  const html = render(withRefs(fixture(), [ref("11"), ref("12", { kind: "pull_request" })]));
  const text = textOf(html);

  it("drops the title, the workspace id and the session ids", () => {
    expect(text).not.toContain("Private title zz");
    expect(text).not.toContain("ws-private-id-7q");
    expect(text).not.toContain("session-ws-private-id-7q");
    expect(text).not.toContain("session-child-id");
    expect(text).not.toContain("grove-ws-private-id-7q");
  });

  it("drops every date, whatever the format", () => {
    expect(text).not.toMatch(/\d{4}-\d{2}-\d{2}/);
    expect(text).not.toMatch(/\bT\d{2}:\d{2}/);
    expect(text).not.toMatch(/\bago\b|\bcreated\b|\bupdated\b/i);
  });

  it("drops the branch diff line counts, which belong to the Changes tab", () => {
    // The working-tree dirty/ahead/behind figures are KEPT (see the Worktree
    // section); only the added/removed LINE totals are out.
    for (const figure of ["321", "654"]) expect(text).not.toContain(figure);
    expect(text).not.toMatch(/\+\s?321|[-−]\s?654/);
  });

  it("drops cache, TTFT, reasoning and latency, which belong to the Info tab", () => {
    expect(text).not.toMatch(/cache|ttft|time to first|reasoning|latency/i);
  });

  it("is plain text: no buttons, no nested tooltip, no scroll container", () => {
    expect(html).not.toMatch(/<button\b/i);
    expect(html).not.toMatch(/role="button"/);
    expect(html).not.toMatch(/data-slot="(?:tooltip|popover)[^"]*"/);
    expect(html).not.toMatch(/aria-haspopup|data-state="(?:open|closed)"/);
    expect(html).not.toMatch(/overflow-(?:y-|x-)?(?:auto|scroll)/);
  });

  it("renders with no provider around it (it IS the tooltip's content)", () => {
    expect(() => render(fixture())).not.toThrow();
  });
});

describe("the primary session, and ONLY the primary", () => {
  const text = textOf(render(fixture()));

  const html = render(fixture());

  it("shows the primary's model, each clock in its own row, tokens, context and native cost", () => {
    expect(field(html, "Model")).toBe(PRIMARY.model);
    expect(field(html, "Tool")).toBe(duration(PRIMARY.toolMs));
    expect(field(html, "Wait")).toBe(duration(PRIMARY.generationMs));
    expect(field(html, "Compute")).toBe(duration(PRIMARY.executionMs));
    expect(field(html, "Clock")).toBe(duration(PRIMARY.activeMs));
    expect(field(html, "Tokens")).toBe(
      `${formatTokenCount(PRIMARY.tokensIn)} in · ${formatTokenCount(PRIMARY.tokensOut)} out`,
    );
    expect(field(html, "Context")).toBe(contextText(PRIMARY.context));
    expect(field(html, "Cost")).toBe(USD_EXACT);
  });

  it("reads Compute time from execution_ms and never from the elapsed span", () => {
    // Labour, wall clock and birth-to-last-event are three different durations.
    // The decoy elapsed span is distinct from both shown clocks, so it can only
    // appear if a row was wired to the wrong field.
    expect(field(html, "Compute")).not.toBe(field(html, "Clock"));
    expect(text).not.toContain(duration(PRIMARY.elapsedMs));
    expect(text).not.toContain(duration(CHILD.elapsedMs));
  });

  it("never lets a child session's figure appear beside the primary's", () => {
    // `sessions[1]` is a sub-agent. If the card summed, picked the busiest or
    // read the last row, one of these would surface.
    expect(text).not.toContain(CHILD.model);
    for (const ms of [CHILD.toolMs, CHILD.generationMs, CHILD.executionMs, CHILD.activeMs]) {
      expect(text).not.toContain(duration(ms));
    }
    expect(text).not.toContain(formatTokenCount(CHILD.tokensIn));
    expect(text).not.toContain(formatTokenCount(CHILD.tokensOut));
    expect(text).not.toContain(contextText(CHILD.context));
    expect(text).not.toContain(USD_CHILD);
  });

  it("does not fill a primary's gaps from a populated child", () => {
    // The incident shape: the primary reported NOTHING and the child reported
    // everything, which is exactly when "first row with data" would be wrong.
    const [first, child] = fixture().sessions;
    const silent: Session = withSession(first, {
      activity: {
        model: null,
        context: null,
        native: null,
        tokens_in: 0,
        tokens_out: 0,
      },
      duration: null,
      tokens: null,
    });
    const gappyHtml = render({ ...fixture(), sessions: [silent, child] });
    const gappy = textOf(gappyHtml);

    // Every row the primary left empty says so, individually...
    for (const label of [
      "Model",
      "Tool",
      "Wait",
      "Compute",
      "Clock",
      "Cost",
      "Context",
    ]) {
      expect(field(gappyHtml, label), label).toBe(NOT_REPORTED);
    }
    // ...and none of the populated child's figures filled the gap.
    expect(gappy).not.toContain(CHILD.model);
    for (const ms of [CHILD.toolMs, CHILD.generationMs, CHILD.executionMs, CHILD.activeMs]) {
      expect(gappy).not.toContain(duration(ms));
    }
    expect(gappy).not.toContain(contextText(CHILD.context));
    expect(gappy).not.toContain(USD_CHILD);
    expect(gappy).not.toContain(formatTokenCount(CHILD.tokensIn));
  });
});

describe("zero is a measurement, absence is not", () => {
  it("prints real zeros as zeros and never as Not reported", () => {
    const base = fixture();
    const [first, child] = base.sessions;
    const zeroed: Session = withSession(first, {
      activity: {
        model: PRIMARY.model,
        tokens_in: 0,
        tokens_out: 0,
        context: { size: 200_000, used: 0, used_fraction: 0 },
        native: { cost_usd: 0, permission_denials: 0 },
      },
      duration: {
        tool_ms: 0,
        generation_ms: 0,
        execution_ms: 0,
        active_ms: 0,
        elapsed_span_ms: 0,
        confidence: "measured",
      },
    });
    const html = render({ ...base, sessions: [zeroed, child] });
    const text = textOf(html);

    // Every measurement row the session reported as zero reads as that zero.
    for (const label of ["Tool", "Wait", "Compute", "Clock"]) {
      expect(field(html, label), label).toBe(duration(0));
    }
    expect(field(html, "Tokens")).toBe("0 in · 0 out");
    expect(field(html, "Context")).toBe(contextText({ used: 0, size: 200_000 }));
    expect(field(html, "Cost")).toMatch(ZERO_USD);
    expect(text).not.toContain(NOT_REPORTED);
    // The child's figures still must not stand in for the primary's zeros.
    expect(text).not.toContain(duration(CHILD.toolMs));
    expect(text).not.toContain(USD_CHILD);
  });

  it("prints Not reported for each null measurement, never a fabricated zero", () => {
    const base = fixture();
    const [first] = base.sessions;
    const silent: Session = withSession(first, {
      activity: { model: null, context: null, native: null },
      duration: null,
    });
    const html = render({ ...base, sessions: [silent] });

    for (const label of ["Model", "Tool", "Wait", "Compute", "Clock", "Cost", "Context"]) {
      expect(field(html, label), label).toBe(NOT_REPORTED);
    }
    expect(textOf(html)).not.toMatch(ZERO_USD);
    expect(textOf(html)).not.toMatch(/\b0s\b/);
  });

  it("distinguishes a null cost from a zero cost on otherwise identical sessions", () => {
    const base = fixture();
    const [first] = base.sessions;
    const costed = (cost: number | null) =>
      field(
        render({
          ...base,
          sessions: [
            withSession(first, {
              activity: {
                ...PRIMARY_ACTIVITY(),
                native: { cost_usd: cost, permission_denials: 0 },
              },
              duration: first.duration,
            }),
          ],
        }),
        "Cost",
      );

    // Same session in every other respect: only the cost's own value differs.
    expect(costed(0)).toMatch(ZERO_USD);
    expect(costed(null)).toBe(NOT_REPORTED);
    // Four decimals survive, as on the Info tab, instead of rounding to cents.
    expect(costed(1.2345)).toBe(USD_EXACT);
  });

  it("reports a workspace with no session at all as unreported, without throwing", () => {
    const html = render(workspace({ id: "none", hasSession: false }));

    for (const label of ["Model", "Tokens", "Tool", "Cost", "Context"]) {
      expect(field(html, label), label).toBe(NOT_REPORTED);
    }
    expect(textOf(html)).not.toContain(CHILD.model);
  });
});

function PRIMARY_ACTIVITY(): Partial<Session["activity"]> {
  return {
    model: PRIMARY.model,
    tokens_in: PRIMARY.tokensIn,
    tokens_out: PRIMARY.tokensOut,
    context: PRIMARY.context,
  };
}

describe("session status", () => {
  it("shows the phase as an icon and its canonical Sentence-case word, with the note as plain text", () => {
    const html = render(fixture());
    const status = section(html, "Session status");

    expect(field(status, "Phase")).toBe("Build");
    expect(field(status, "Note")).toBe("rebasing onto main");
    expect(field(status, "Phase")).not.toMatch(/\bbuild\b/);
    // The phase mark is an icon beside the word, not a badge or an action.
    expect(status.match(/<dt>Phase<\/dt><dd>([\s\S]*?)<\/dd>/)![1]).toMatch(/<svg\b/);
    expect(html).not.toMatch(/data-slot="badge"/);
    expect(html).not.toMatch(/<button\b/);
    // The note is prose, not a code literal.
    expect(codes(html)).not.toContain("rebasing onto main");
  });

  it("says blocked, and where it stopped, rather than only the bare phase", () => {
    const html = render(fixture({ phase: phaseView({ phase: "verify", index: 3, blocked: true }) }));

    expect(field(html, "Phase")).toBe("Verify · Blocked");
  });

  it("does not call an unblocked phase blocked", () => {
    expect(field(render(fixture()), "Phase")).not.toMatch(/blocked/i);
  });

  it("previews a long note in at most two lines via the stylesheet, and keeps the whole note", () => {
    const long = `${"A very long phase note that keeps going. ".repeat(20)}END-OF-NOTE`;
    const html = render(fixture({ phase: phaseView({ note: long }) }));

    // The clamp lives in the stylesheet that owns it: the markup names the
    // class, the rule bounds it. A utility `line-clamp-*` would be a second,
    // competing decision about the same line count.
    expect(html).toContain('class="session-overview-note"');
    const clamp = rule(".session-overview-note");
    expect(clamp).toMatch(/-webkit-line-clamp:\s*2\b/);
    expect(clamp).toMatch(/-webkit-box-orient:\s*vertical/);
    expect(clamp).toMatch(/overflow:\s*hidden/);
    expect(html).not.toMatch(/line-clamp-/);
    // Clamping is presentation only: the whole note is still in the markup.
    expect(field(html, "Note")).toContain("END-OF-NOTE");
  });

  it("states an absent phase note instead of leaving the row blank", () => {
    expect(field(render(fixture({ phase: phaseView({ note: null }) })), "Note")).toBe(
      "No phase note reported",
    );
  });

  it("shows the unread cue only when told the workspace is unread", () => {
    // The cue rides the Status row; a separate Attention row only repeated it.
    expect(field(render(fixture(), true), "Status")).toContain("Unread");
    for (const html of [render(fixture(), false), render(fixture())]) {
      expect(html).not.toMatch(/<dt>Attention<\/dt>/);
      expect(textOf(html)).not.toMatch(/unread/i);
    }
  });
});

describe("attached tickets", () => {
  const tickets = (html: string): string => section(html, "Attached tickets");

  it("says nothing is attached, and draws no rollup, in the tickets section", () => {
    const html = render(withRefs(fixture(), []));
    const body = textOf(tickets(html));

    expect(body).toContain("No tickets attached");
    // Scoped on purpose: the Context row legitimately prints a percentage.
    expect(body).not.toMatch(/%/);
    expect(body).not.toMatch(/at handoff|unreported|blocked/i);
    expect(tickets(html)).not.toMatch(/data-slot="progress"/);
    expect(ticketRows(html)).toBe(0);
  });

  it("shows the rollup as the existing mean of index / (total - 1), silence included", () => {
    // One finished ticket and one nobody reported on: (1 + 0) / 2 = 50%.
    // `index / total` would read (5/6 + 0) / 2 = 42%, the off-by-one the
    // selector's own test names.
    const refs = [ref("11"), ref("12")];
    const phase = phaseView({}, [claim("gitea:11", "handoff", 5)]);
    const body = textOf(tickets(render(withRefs(fixture(), refs, phase))));

    expect(ticketRollup(refs, phase)!.fraction).toBe(0.5);
    expect(body).toMatch(/\b50%/);
    expect(body).not.toMatch(/\b4[12]%/);
    expect(body).not.toMatch(/\b100%/);
    expect(body).toContain("1 of 2 at handoff");
    // The denominator survives in the Done row: a percentage never renders alone.
    expect(body).toContain("1 unreported");
    expect(body).toContain("1 unreported");
    // The selector's own coverage sentence is unchanged and still true.
    expect(rollupCoverage(ticketRollup(refs, phase)!)).toBe("1 / 2 done · 1 claim not reported");
  });

  it("names the silence and the block instead of folding them into the percentage", () => {
    const refs = [ref("11"), ref("12"), ref("13")];
    const phase = phaseView({}, [claim("gitea:11", "build", 2, true), claim("gitea:12", "verify", 3)]);
    const body = textOf(tickets(render(withRefs(fixture(), refs, phase))));

    // (2/5 + 3/5 + 0) / 3 = 33.3%: the unreported third drags it, and says so.
    expect(body).toMatch(/\b33%/);
    expect(body).toContain("0 of 3 at handoff");
    expect(body).toContain("1 unreported");
    expect(body).toContain("1 blocked");
  });

  it("keeps a blocked ticket's reached position in the percentage", () => {
    const refs = [ref("11")];
    const phase = phaseView({}, [claim("gitea:11", "deliver", 4, true)]);
    const html = render(withRefs(fixture(), refs, phase));
    const body = textOf(tickets(html));

    expect(body).toMatch(/\b80%/);
    expect(body).toContain("1 blocked");
    expect(body).toContain("0 of 1 at handoff");
    // Blocked is a flag on the position, shown on the row too.
    expect(body).toContain("Deliver · Blocked");
  });

  it("counts a ticket that is both handed off and blocked as done", () => {
    const refs = [ref("11")];
    const phase = phaseView({}, [claim("gitea:11", "handoff", 5, true)]);
    const body = textOf(tickets(render(withRefs(fixture(), refs, phase))));

    expect(body).toContain("1 of 1 at handoff");
    expect(body).toContain("1 blocked");
    expect(body).toMatch(/\b100%/);
  });

  it("marks a ticket nobody reported on as Unreported, never as a first phase", () => {
    const refs = [ref("11"), ref("12")];
    const phase = phaseView({}, [claim("gitea:11", "verify", 3)]);
    const body = tickets(render(withRefs(fixture(), refs, phase)));
    const rows = body.split('data-testid="session-overview-ticket"').slice(1);

    expect(rows).toHaveLength(2);
    const byId = (id: string) => textOf(rows.find((row) => row.includes(`#${id}`))!);
    expect(byId("11")).toContain("Verify");
    expect(byId("12")).toContain("Unreported");
    expect(byId("12")).not.toMatch(/Scope|Plan|Build|Verify|Deliver|Handoff/);
  });

  describe("the three-ref preview", () => {
    const issues = ["11", "12", "13", "14", "15", "16"].map((id) => ref(id));
    // The PR is attached LAST, after six issues: a plain `slice(0, 3)` of the
    // attachment order never shows it.
    const latePr = ref("99", { kind: "pull_request", status: "open" });
    const html = render(withRefs(fixture(), [...issues, latePr]));
    const body = tickets(html);

    it("shows an attached pull request even when it came after the first three issues", () => {
      expect(textOf(body)).toContain("#99");
    });

    it("previews exactly three refs and counts the rest", () => {
      expect(ticketRows(html)).toBe(3);
      expect(body.match(/#\d+/g)).toHaveLength(3);
      expect(textOf(body)).toContain("+4 more");
      expect(count(html, 'data-testid="session-overview-overflow"')).toBe(1);
    });

    it("counts the header total over every attached ticket, not the preview", () => {
      expect(textOf(body)).toContain("7 tickets");
    });

    it("shows every ref and no overflow when there are three or fewer", () => {
      const few = render(withRefs(fixture(), [ref("21"), ref("22"), latePr]));

      expect(ticketRows(few)).toBe(3);
      expect(few).not.toContain("session-overview-overflow");
      expect(textOf(tickets(few))).not.toMatch(/\+\d+ more/);
    });

    it("counts overflow off the total, not off what the preview happened to drop", () => {
      const eight = render(withRefs(fixture(), [...issues, ref("17"), latePr]));

      expect(ticketRows(eight)).toBe(3);
      expect(textOf(tickets(eight))).toContain("+5 more");
      expect(textOf(tickets(eight))).toContain("#99");
    });

    it("keeps the late PR when it is the only pull request among many issues, however many", () => {
      const many = render(
        withRefs(fixture(), [...Array.from({ length: 20 }, (_, i) => ref(`${100 + i}`)), latePr]),
      );

      expect(ticketRows(many)).toBe(3);
      expect(textOf(tickets(many))).toContain("#99");
      expect(textOf(tickets(many))).toContain("+18 more");
    });
  });

  it("carries no tracker state column", () => {
    const refs = [
      ref("11", { status: "open" }),
      ref("12", { status: "closed" }),
      ref("13", { kind: "pull_request", status: "merged" }),
    ];
    const body = tickets(render(withRefs(fixture(), refs)));

    // The tracker's open/closed/merged is a different axis from the agent's
    // phase, and this section shows the phase axis only. Scoped to the
    // section: a branch or note elsewhere may legitimately use these words.
    // Two columns, ticket then agent phase, with no tracker-state column.
    expect(body).not.toContain("<th");
    expect(body.split('data-testid="session-overview-ticket"')[1]!.match(/<td>/g)).toHaveLength(2);
    expect(textOf(body)).not.toMatch(/\b(?:open|closed|merged|draft)\b/i);
  });
});

describe("worktree", () => {
  const worktree = (ws: WorkspaceActivityView): string => section(render(ws), "Worktree");

  it("prefers the live branch over the stored one, as inline code", () => {
    const body = worktree(fixture());

    expect(codes(body)).toContain("live/top-branch");
    expect(textOf(body)).not.toContain("stale/state-branch");
  });

  it("falls back to the stored branch when the view carries no live one", () => {
    const { branch: _live, ...rest } = fixture();

    expect(codes(worktree(rest as WorkspaceActivityView))).toContain("stale/state-branch");
    expect(codes(worktree({ ...fixture(), branch: "" }))).toContain("stale/state-branch");
  });

  it("shows the base branch as inline code", () => {
    expect(codes(worktree(fixture()))).toContain("release-base");
  });

  it("keeps the working-tree counts, and the divergence from the base when it is another branch", () => {
    expect(field(worktree(fixture()), "Changes")).toBe("7 uncommitted · 4 ahead · 9 behind");
  });

  it("keeps a zero working-tree count as a zero", () => {
    const clean = fixture({ dirty_files: 0, base_ahead: 0, base_behind: 0 });

    expect(field(worktree(clean), "Changes")).toBe("0 uncommitted · 0 ahead · 0 behind");
  });

  it("drops ahead and behind on the base branch itself, where they cannot be anything but zero", () => {
    const onBase = fixture({ branch: "release-base" });

    expect(field(worktree(onBase), "Changes")).toBe("7 uncommitted");
  });

  it("shows a SHORT commit as inline code, never the full hash", () => {
    const body = worktree(fixture());
    const full = "abcdef1234567890abcdef1234567890abcdef12";
    const short = codes(body).find((code) => full.startsWith(code) && code.length >= 7);

    expect(short).toBeDefined();
    expect(short!.length).toBeLessThan(full.length);
    expect(textOf(body)).not.toContain(full.slice(0, 20));
  });

  it("states there is no commit rather than inventing one", () => {
    const body = worktree(fixture({ recent_commits: [] }));

    expect(field(body, "Commit")).toBe("No commits reported");
    expect(codes(body).some((code) => /^[0-9a-f]{7,}$/.test(code))).toBe(false);
  });
});

describe("bounded layout", () => {
  it("owns its measure in the tooltip stylesheet rule, with no scroll and no utility width", () => {
    const html = render(withRefs(fixture(), Array.from({ length: 12 }, (_, i) => ref(`${i + 30}`))));
    const tooltip = rule(".session-overview-tooltip");

    // The measure belongs to the tooltip rule, so it is asserted there rather
    // than as a utility class the card might or might not carry.
    expect(tooltip).toMatch(/width:\s*min\(/);
    expect(tooltip).toMatch(/max-width:\s*var\(--radix-tooltip-content-available-width\)/);
    expect(tooltip).not.toMatch(/overflow(?:-[xy])?:\s*(?:auto|scroll)/);
    expect(tooltip).not.toMatch(/max-height/);
    expect(html).not.toMatch(/overflow-(?:y-|x-)?(?:auto|scroll)|max-h-|\bh-\[/);
    expect(ticketRows(html)).toBe(3);
  });

  it("clips long single-line values instead of growing the card", () => {
    expect(rule(".session-overview-name")).toMatch(/text-overflow:\s*ellipsis/);
    expect(rule(".session-overview-name")).toMatch(/white-space:\s*nowrap/);
  });
});
