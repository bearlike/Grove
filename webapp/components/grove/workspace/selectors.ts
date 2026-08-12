import { FancyAnsi } from "fancy-ansi";
import type { VariantProps } from "class-variance-authority";
import {
  CircleCheckIcon,
  CircleDashedIcon,
  CircleDotIcon,
  GitMergeIcon,
  GitPullRequestClosedIcon,
  GitPullRequestDraftIcon,
  GitPullRequestIcon,
  type LucideIcon,
} from "lucide-react";

import type {
  CommitSummaryView,
  DurationView,
  GenerationLatencyView,
  PhaseView,
  SessionSummaryView,
  TicketRef,
  WorkspaceActivityView,
  WorkspaceStateView,
} from "@/lib/grove/api";
import type { badgeVariants } from "@/components/ui/badge";
import type { TimelineEvent } from "@/components/elements/timeline";
import type { GlossaryTerm } from "@/components/grove/glossary";
// TYPE-ONLY, so it is erased and there is no runtime cycle: `fleet/badges.tsx`
// imports `phaseTooltip` from this file as a value, and this import back is a
// shape, not a module edge.
import type { PhaseMark } from "@/components/grove/fleet/badges";
import type { TaskPhase } from "@/components/grove/fleet/types";

/**
 * Presentation rules that belong to the workspace surface alone.
 *
 * Anything a second surface would also need — the activity-snapshot lookups,
 * the transcript mapping, the question and todo shapes — lives in
 * `lib/grove/adapters` instead. What is left here is genuinely local: which
 * pane is showing, which lifecycle verbs to offer, and how this surface labels
 * its own rows.
 */

/**
 * Which work-panel surfaces exist, IN THE ORDER THE STRIP RENDERS THEM — the
 * one census, from which the type is derived rather than written twice.
 *
 * There were three copies of this list a moment ago: this union, a validation
 * array beside `restoredWorkTab`, and `work-panel.tsx`'s own label/icon tuples.
 * Three places to keep in sync is how a sixth tab ends up unreachable from the
 * restore path while still rendering, which is invisible until someone reloads
 * onto it. `work-panel.tsx` now maps over this array and looks its label and
 * icon up in a `Record<PanelTab, …>`, so a tab added here without a label
 * fails to compile instead of silently vanishing from the strip.
 */
export const PANEL_TAB_VALUES = ["terminal", "changes", "files", "info", "controls"] as const;

/** Which work-panel surface is showing. */
export type PanelTab = (typeof PANEL_TAB_VALUES)[number];

/** The workspace's two panes; `split` shows both at once. */
export type PaneView = "transcript" | "work" | "split";

/** A single mountable pane — what `PaneView` resolves to, one at a time or both. */
export type PaneKey = "transcript" | "work";

/**
 * Which panes a resolved `PaneView` shows right now. `split` shows both at
 * once; a single-pane view shows only itself.
 *
 * This is the seam that drives lazy, once-only mounting in `Workspace`: a
 * pane's mounted flag latches true the first render where its key appears
 * here, and never latches back false, so a pane already visited stays
 * mounted (and merely hidden) rather than being torn down and rebuilt on
 * every switch back to it.
 */
export function panesShown(paneView: PaneView): readonly PaneKey[] {
  return paneView === "split" ? ["transcript", "work"] : [paneView];
}

/**
 * The pane actually showing, given what the viewport can offer.
 *
 * `split` is retired from the switcher below the breakpoint, but the chosen
 * view is component state and does not retire with it — so narrowing a window
 * while split was selected left NO tab reporting `aria-selected` and unmounted
 * the work panel, and the pane the user had picked simply vanished.
 *
 * The fallback is `work`, not `transcript`: someone in split view is there for
 * the work panel, and dropping them on the transcript discards the half they
 * opened split to see. Pure, and read during render rather than corrected by an
 * effect, so the switcher and the content cannot disagree even for one frame.
 */
export function visiblePane(view: PaneView, splitOffered: boolean): PaneView {
  return view === "split" && !splitOffered ? "work" : view;
}

/**
 * The work-panel tab a pane change should land on — `null` to keep whichever
 * one is showing.
 *
 * Entering the work panel — whether alone or via SPLIT — always lands on
 * Info first. Terminal used to be the work-alone default on the reasoning
 * that "let me drive the agent" was the pane's whole point; a design review
 * overrode that, because landing on a bare terminal with no context read as
 * "taken to the wrong place" regardless of which switcher tab was clicked.
 *
 * Keyed on the TRANSITION, not on the destination, and that is the whole
 * subtlety: a deliberate tab choice made WHILE ALREADY on that pane has to
 * survive (picking Terminal inside Work, then toggling the switcher off it
 * and back, must not throw the choice away), and widening a window back into
 * a split the user had already arranged must not silently move them either.
 * Pure, so the rule is pinned by a test rather than by a render.
 */
export function tabOnViewChange(from: PaneView, to: PaneView): PanelTab | null {
  return to !== from && (to === "split" || to === "work") ? "info" : null;
}

const PANE_VIEWS: readonly PaneView[] = ["transcript", "work", "split"];

/**
 * The `view` to restore from a raw, `localStorage`-sourced value — untyped
 * because it crossed a boundary the type system cannot see through: a
 * missing key, a value written by an older or newer build, or a hand-edited
 * store all have to fall back rather than crash or render a pane that does
 * not exist. Anything that is not one of the three known values falls back
 * to "transcript", the same default a workspace with no stored choice at
 * all starts on.
 *
 * ALWAYS resolved through `visiblePane` before being handed back — a `split`
 * persisted from a wide session, restored on a narrow one, must not leave no
 * tab selected any more than a live width change may. Restoring is just
 * another route to the same state `visiblePane` already guards.
 */
export function restoredView(stored: unknown, splitOffered: boolean): PaneView {
  const candidate = (PANE_VIEWS as readonly unknown[]).includes(stored) ? (stored as PaneView) : "transcript";
  return visiblePane(candidate, splitOffered);
}

/**
 * The `workTab` to restore from a raw, `localStorage`-sourced value. Same
 * defensiveness as `restoredView`, and the fallback is "info" for the same
 * reason `workTab`'s own initial state is "info" in `Workspace` — it has to
 * agree with `tabOnViewChange`'s landing tab, or the very first restored
 * entry into Work/Split would flash the wrong tab for one render.
 */
export function restoredWorkTab(stored: unknown): PanelTab {
  return (PANEL_TAB_VALUES as readonly unknown[]).includes(stored) ? (stored as PanelTab) : "info";
}

export type LifecycleAction = "pause" | "resume" | "respawn" | "kill";

/**
 * The lifecycle verbs worth offering for a status.
 *
 * A pure UX mirror of the engine's own gate, never a re-implementation of it:
 * a stale snapshot offering an illegal verb just surfaces the daemon's typed
 * refusal. Root placement drops pause and resume, which the engine refuses
 * anyway, and an unrecognised status falls back to the one verb that always
 * applies.
 */
export function availableActions(state: WorkspaceStateView): readonly LifecycleAction[] {
  const suspendable = state.placement !== "root";
  switch (state.status) {
    case "active":
    case "running":
    case "idle":
      return suspendable ? ["pause", "kill"] : ["kill"];
    case "paused":
      return suspendable ? ["resume", "kill"] : ["kill"];
    case "offline":
      return ["respawn", "kill"];
    default:
      return ["kill"];
  }
}

/**
 * What `kill(delete_branch=null)` resolves to engine-side, mirrored only so the
 * confirm dialog can pre-tick an honest default.
 */
export function defaultDeleteBranch(state: WorkspaceStateView): boolean {
  if (state.placement === "root") return false;
  return state.branch_provenance === "grove";
}

/** One labelled figure from the live activity snapshot. */
export type ActivityStat = { label: string; value: string };

/**
 * The activity readout as separate figures rather than one packed line.
 *
 * Labelled numbers laid across the card read at a glance and use the width
 * the panel actually has; the same figures joined by interpuncts were a
 * sentence that had to be parsed, and wrapped badly the moment the pane was
 * narrowed. Token counts are abbreviated because their magnitude is the
 * signal — nobody reads the units digit of 1,284,193.
 *
 * `tokens in` appears here ONLY when `tokenClassStats` has nothing to show —
 * an older daemon that has not shipped the class breakdown yet, or a
 * fleet-entry row `ActivityService` never resolved a message spine for. The
 * two are never shown together: a folded total beside its own unfolding
 * would restate the same magnitude twice, once as a mystery and once
 * explained.
 */
export function activityStats(activity: WorkspaceActivityView | null): ActivityStat[] | null {
  const session = activity?.sessions[0];
  const live = session?.activity;
  if (!live) return null;
  const stats: ActivityStat[] = [
    { label: "turns", value: COUNT.format(live.human_turns) },
    { label: "tool calls", value: COUNT.format(live.tool_calls) },
  ];
  if (!session?.tokens) {
    stats.push({ label: "tokens in", value: COMPACT.format(live.tokens_in) });
  }
  stats.push({ label: "tokens out", value: COMPACT.format(live.tokens_out) });
  return stats;
}

/** One class of `tokens in`, with the glossary term that explains it (if any). */
export type TokenClassStat = { label: string; value: string; term?: GlossaryTerm };

/**
 * `tokens in` unfolded into the classes that sum to it, so a figure in the
 * hundreds of millions reads as "mostly cache reads" instead of as a bug.
 * `AgentActivityView.tokens_in` folds fresh input, cache read and cache
 * creation together BY DESIGN (see its engine docstring) — cache reads are
 * routinely an order of magnitude cheaper than fresh input and dwarf every
 * other class, so the fold alone cannot explain the number it reports, only
 * produce it.
 *
 * `null` when the wire carries no breakdown for this session (see
 * `activityStats`'s docstring for when and why) — the caller falls back to
 * the folded total in that case rather than rendering nothing.
 *
 * Each class renders "not measured" rather than a fabricated `0`: an absent
 * count here means this specific class was never reported, not that it was
 * measured at zero. `reasoning` and `provider_total` are left off this
 * surface deliberately — they answer a different question (a provider's
 * informational split, its own stated total) than "why is tokens-in this
 * big", which fresh input / cache read / cache creation already answer
 * completely, since those three are exactly what `tokens_in` sums.
 */
export function tokenClassStats(activity: WorkspaceActivityView | null): TokenClassStat[] | null {
  const tokens = activity?.sessions[0]?.tokens;
  if (!tokens) return null;
  const format = (n: number | null | undefined) => (n == null ? "not measured" : COMPACT.format(n));
  return [
    { label: "fresh input", value: format(tokens.fresh_input) },
    { label: "cache read", value: format(tokens.cache_read), term: "cache_read_tokens" },
    { label: "cache write", value: format(tokens.cache_creation), term: "cache_creation_tokens" },
  ];
}

/**
 * The session's two clocks, or `null` when it reports neither.
 *
 * Reads `sessions[0]` — the SAME row `activityStats` takes its four figures
 * from — so the Timeline's clocks and the Activity card's counts describe one
 * session. A second lookup here would be free to disagree, and silently.
 *
 * BOTH-OR-NEITHER is the point of returning `null` rather than a view with two
 * nulls in it. `duration()` already refuses to fabricate a zero for one absent
 * field; without this guard a workspace with no live session would still print
 * two rows reading "not measured", which claims the clocks exist and were not
 * taken rather than that there is nothing here to time. A confidence of
 * `unknown` is NOT that case — the session was measured, Grove is just telling
 * you how well, so those rows still render.
 */
export function sessionClocks(activity: WorkspaceActivityView | null): DurationView | null {
  return activity?.sessions[0]?.duration ?? null;
}

/**
 * The model's own average response wait for this session, or `null` when the
 * wire carries nothing (an older daemon, or a row `duration`/`tokens` are
 * also null for). Reads the SAME `sessions[0]` those two already take their
 * figures from, for the reason `sessionClocks` states above — one lookup, so
 * the clocks and this cannot describe different sessions.
 */
export function sessionLatency(
  activity: WorkspaceActivityView | null,
): GenerationLatencyView | null {
  return activity?.sessions[0]?.latency ?? null;
}

const COUNT = new Intl.NumberFormat("en-US");
const COMPACT = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });

/** Commits as the vendored `Timeline`'s event shape; the newest commit is "now". */
export function commitEvents(commits: readonly CommitSummaryView[]): TimelineEvent[] {
  return commits.map((commit, index) => ({
    id: commit.sha,
    when: index === 0 ? "now" : "past",
    time: commit.sha.slice(0, 7),
    title: commit.subject,
    detail: formatTime(commit.committed_at),
  }));
}

/**
 * One ticket's own phase claim, INDEXED OUT OF THE PARENT VIEW rather than
 * restated — the same construction `fleet/types.ts` uses, so a field the daemon
 * adds to the row cannot drift from what this file believes it sent.
 */
export type TicketPhaseView = PhaseView["tickets"][number];

/**
 * A ticket's claim widened to what a `PhaseBadge` draws.
 *
 * The wire row carries no `total` of its own because it is a position in the
 * SAME phase order its parent counts, so the total comes from the parent — and
 * this surface never holds a second copy of how many phases there are.
 */
export type TicketPhaseMark = TicketPhaseView & { total: number };

/**
 * How `PhaseView.tickets` addresses a ticket: `provider:id`, and deliberately
 * NOT the three-part `ticketKey` the resolve cache uses.
 *
 * KIND IS ABSENT ON PURPOSE, and it is what makes this join survive a
 * correction. A forge numbers issues and pull requests in one space, so
 * `gitea:42` already names one object; and a ref attached as an issue that
 * resolves to a pull request — the exact case `ticketKey`'s own comment warns
 * about — keeps its coordinate, so the agent's claim cannot detach from the row
 * it belongs to.
 */
export function ticketPhaseKey(ref: Pick<TicketRef, "provider" | "id">): string {
  return `${ref.provider}:${ref.id}`;
}

/**
 * The agent's per-ticket claims, keyed for the rows that will draw them.
 *
 * A LOOKUP THAT CAN MISS, never a value per ref, because absence of a claim is
 * not a claim. An agent working three issues is at a different point on each,
 * and a ticket it has said nothing about must not inherit the workspace's own
 * phase — that would invent progress on work nobody reported. A miss renders no
 * mark at all, which is what "not reported" looks like everywhere else this
 * axis appears.
 *
 * Pure and client-side: both halves are already on the Info tab, so joining
 * them costs no request, no hook and no loading state of its own.
 */
export function ticketPhases(
  phase: PhaseView | null | undefined,
): ReadonlyMap<string, TicketPhaseMark> {
  if (!phase) return new Map();
  return new Map(
    phase.tickets.map((claim) => [claim.ticket, { ...claim, total: phase.total }] as const),
  );
}

/**
 * How far the workspace has got across EVERY ticket it holds, as one figure.
 *
 * A reader scanning a workspace wants "is this batch nearly done" before they
 * want any one ticket's position, and six separate marks do not answer that —
 * counting them is work the surface should have done.
 *
 * **An unclaimed ticket counts as zero rather than being excluded**, and that is
 * the whole honesty of the number. Excluding it would let a workspace holding
 * five untouched tickets and one finished one read 100% complete, which is the
 * direction that misleads. Counting it as zero can only ever understate, and
 * `unreported` is returned beside the fraction so the reader can see how much of
 * the estimate is silence rather than measurement.
 *
 * `blocked` is NOT subtracted from `done` or from the fraction. A blocked ticket
 * keeps the position it reached — that is the axis's whole premise — so it still
 * contributes its progress and is surfaced separately as the thing needing a
 * human. A ticket that is both `done` and blocked is counted as done: the agent
 * finished it and flagged something, and hiding the completion would be the
 * stranger claim.
 */
export type TicketRollup = {
  total: number;
  reported: number;
  unreported: number;
  done: number;
  blocked: number;
  /** 0–1 mean completion across every attached ticket, unclaimed ones included. */
  fraction: number;
};

export function ticketRollup(
  refs: readonly TicketRef[],
  phase: PhaseView | null | undefined,
): TicketRollup | null {
  // No tickets means no aggregate to take — NOT a zeroed one. A workspace with
  // nothing attached is not a batch at 0%, and rendering it as one would put a
  // permanently-empty bar on every single-purpose workspace in the fleet.
  if (refs.length === 0) return null;

  const claims = ticketPhases(phase);
  let reported = 0;
  let done = 0;
  let blocked = 0;
  let progress = 0;

  for (const ref of refs) {
    const claim = claims.get(ticketPhaseKey(ref));
    if (!claim) continue;
    reported += 1;
    if (claim.blocked) blocked += 1;
    if (claim.phase === "done") done += 1;
    // `index / (total - 1)` so `done` is exactly 1 and `scoping` exactly 0 —
    // the ramp's endpoints, not an off-by-one that makes a finished ticket
    // read 83%. Guarded because a one-phase vocabulary would divide by zero.
    progress += claim.total > 1 ? claim.index / (claim.total - 1) : 1;
  }

  return {
    total: refs.length,
    reported,
    unreported: refs.length - reported,
    done,
    blocked,
    fraction: progress / refs.length,
  };
}

/** Issues first, then pull requests — the order they appear in over a task's life. */
export function sortTicketRefs(refs: readonly TicketRef[]): TicketRef[] {
  return [...refs].sort(
    (a, b) => Number(a.kind === "pull_request") - Number(b.kind === "pull_request"),
  );
}

type TicketProvider = TicketRef["provider"];
type TicketKind = TicketRef["kind"];
type UiBadgeVariant = NonNullable<VariantProps<typeof badgeVariants>["variant"]>;

/**
 * How each tracker writes its own name. A provider is an IDENTITY, not a state,
 * so it is never a colour and never a badge — a word in the metadata tier.
 *
 * A `Record` over the wire enum rather than a lookup with a fallback: adding a
 * provider to the daemon should fail to compile here, not render as `linear`
 * lowercased and half-branded.
 */
const PROVIDER_LABEL: Record<TicketProvider, string> = {
  gitea: "Gitea",
  github: "GitHub",
  linear: "Linear",
};

const KIND_LABEL: Record<TicketKind, string> = {
  issue: "issue",
  pull_request: "pull request",
};

/**
 * The five states this app draws a ticket in — GROVE'S vocabulary, not a wire
 * enum, and the distinction is load-bearing.
 *
 * `TicketRef.status` is typed `string | null` on the wire: it is whatever the
 * tracker wrote, and trackers disagree (`open`/`opened`/`reopened`, `closed`
 * vs `done`). So there is nothing here to key a `Record` on for compile-time
 * totality. This union is the seam that creates one: provider text is
 * normalized ONCE into these five, and every table downstream is
 * `Record<TicketState, …>` — total by construction, so adding a sixth state
 * fails to compile in the glyph table and the colour table together.
 *
 * `unknown` is a real member rather than a null: a tracker that answers with a
 * word we have never seen still HAS a state, and the row still has to draw
 * something. It renders neutral and spells the tracker's own word out, which is
 * the honest treatment — marked, not interpreted.
 */
export type TicketState = "open" | "closed" | "merged" | "draft" | "unknown";

/**
 * Provider text → Grove's state.
 *
 * Lower-cased and looked up, with a documented fallback rather than a `Record`
 * over the union, precisely BECAUSE the input side is open: this is the one
 * place a new tracker's vocabulary may appear without a type error, and the
 * fallback is what keeps that from being a crash instead of a neutral row.
 */
const STATE_BY_WORD: Record<string, TicketState> = {
  open: "open",
  opened: "open",
  reopened: "open",
  merged: "merged",
  closed: "closed",
  done: "closed",
  completed: "closed",
  draft: "draft",
};

export function ticketState(status: string | null | undefined): TicketState {
  if (!status) return "unknown";
  return STATE_BY_WORD[status.trim().toLowerCase()] ?? "unknown";
}

/**
 * A ticket's state → how loud its badge is.
 *
 * NOTHING HERE IS `default`, AND THAT CHANGED WHEN THE GLYPH GAINED ITS COLOUR.
 * `open` used to be the loudest variant, which was right while the badge was the
 * only mark on the row; now the glyph beside it is a green open-circle, so a
 * solid black pill made three tickets shout the same fact twice — the identical
 * defect the fleet card had when `active` and `working` were both `default`.
 * The glyph is the state mark and the badge is the WORD that makes it survive
 * greyscale, so the word stays quiet.
 *
 * `unknown` is `outline` for a different reason: still marked, still spelled
 * out, just not claimed to mean something.
 */
const STATUS_TONE: Record<TicketState, UiBadgeVariant> = {
  open: "outline",
  merged: "secondary",
  closed: "secondary",
  draft: "outline",
  unknown: "outline",
};

export function providerLabel(provider: TicketProvider): string {
  return PROVIDER_LABEL[provider];
}

export function ticketKindLabel(kind: TicketKind): string {
  return KIND_LABEL[kind];
}

/**
 * How a tracker writes its own id. `#42` on a forge that numbers issues,
 * `ENG-123` verbatim on one that keys them.
 *
 * The `#` is a forge convention, not Grove's, so it is applied to the SHAPE of
 * the id rather than to a provider name — that is what keeps a Linear key from
 * rendering as `#ENG-123`, without this function knowing which providers number
 * and which do not.
 */
export function ticketIdLabel(ref: Pick<TicketRef, "id">): string {
  return /^\d+$/.test(ref.id) ? `#${ref.id}` : ref.id;
}

export function ticketStatusTone(status: string): UiBadgeVariant {
  return STATUS_TONE[ticketState(status)];
}

/**
 * The state colour, as a utility class — the forge convention every developer
 * already reads without being taught: green open, purple merged, red closed,
 * grey draft.
 *
 * A `Record` over the union, never a ternary at a call site: this is the table
 * §4.1 asks for, and a call site that picks a hue is a call site that will
 * disagree with the next one.
 *
 * COLOUR IS THE SECOND CARRIER HERE, NEVER THE FIRST. The glyph shape differs
 * per state (`TICKET_GLYPH`) and the badge beside it prints the tracker's own
 * word, so the row survives greyscale and dichromatic vision with the hue
 * removed entirely — which §4.7 requires and which `--success` vs
 * `--destructive` measurably cannot do on their own.
 */
const STATE_COLOUR: Record<TicketState, string> = {
  open: "text-success",
  merged: "text-merged",
  closed: "text-destructive",
  draft: "text-content-tertiary",
  unknown: "text-content-tertiary",
};

export function ticketStateColour(state: TicketState): string {
  return STATE_COLOUR[state];
}

/**
 * The mark for a ticket, by KIND and STATE — the shape half of §4.7's rule that
 * a state colour is never the sole carrier.
 *
 * Total on both axes on purpose, which forces an answer for the two cells that
 * cannot happen in a healthy tracker. An issue is never `merged`; if a provider
 * ever says so, the truest thing left to draw is "closed by landing", so it
 * takes the same check the closed cell does rather than a git-merge glyph that
 * would claim a branch was involved. Totality over a plausible-looking partial
 * map is what makes a new state a compile error in one place.
 *
 * `CircleCheckIcon`, not the `CheckCircleIcon` alias lucide still resolves —
 * both work, which is exactly the drift §7's spelling rule exists to stop.
 */
const TICKET_GLYPH: Record<TicketKind, Record<TicketState, LucideIcon>> = {
  issue: {
    open: CircleDotIcon,
    closed: CircleCheckIcon,
    merged: CircleCheckIcon,
    draft: CircleDashedIcon,
    unknown: CircleDotIcon,
  },
  pull_request: {
    open: GitPullRequestIcon,
    closed: GitPullRequestClosedIcon,
    merged: GitMergeIcon,
    draft: GitPullRequestDraftIcon,
    unknown: GitPullRequestIcon,
  },
};

export function ticketGlyph(kind: TicketKind, state: TicketState): LucideIcon {
  return TICKET_GLYPH[kind][state];
}

/**
 * What each phase word MEANS, for a reader who has never read the skill that
 * defines it.
 *
 * `◐ 3/6` is a position in a vocabulary the reader was never taught — the mark
 * says how far along something is without ever saying what "along" is measured
 * in, which is why the badge reads as an arbitrary process. These sentences are
 * the same six the agent is given in `skills/working-in-grove/SKILL.md`, turned
 * around: the skill writes them TO the agent ("You are editing files") and this
 * is read BY a human watching it, so the agent becomes the subject. Substance
 * unchanged — if that table is edited, edit this one, because two different
 * explanations of one word teach it twice and trust neither (see `glossary.tsx`).
 *
 * A `Record` over the wire union rather than a lookup with a fallback: a seventh
 * phase must fail to compile here, not render as a badge that explains nothing —
 * which is the exact defect this table exists to remove.
 */
const PHASE_MEANING: Record<TaskPhase, string> = {
  scoping:
    "The agent is reading the ticket, the code and the tests, working out what the job actually is.",
  planning: "The agent understands the problem and is choosing an approach or writing it down.",
  implementing: "The agent is editing files.",
  verifying:
    "The agent is running tests, linters or the build, and reading its own diff back.",
  delivering:
    "The agent is committing, pushing, opening or updating the pull request, writing the handoff.",
  done: "Handed off — nothing is left for the agent to do here.",
};

/**
 * The tracker's own word for a state, as the second half of a sentence.
 *
 * `unknown` is deliberately `null` rather than a word: a state Grove could not
 * normalize has no controlled term to assert, so the sentence quotes whatever
 * the tracker actually wrote instead of inventing one. Same treatment
 * `TicketState`'s own docstring gives it — marked, not interpreted.
 */
const TRACKER_WORD: Record<TicketState, string | null> = {
  open: "open",
  closed: "closed",
  merged: "merged",
  draft: "a draft",
  unknown: null,
};

/** What a phase tooltip needs of a ticket: who tracks it, and what they call it. */
export type PhaseTooltipTicket = Pick<TicketRef, "provider" | "kind" | "status">;

/**
 * The phase badge's hover, as separate claims rather than one paragraph.
 *
 * Each field is one sentence at most, and each is a DIFFERENT claim by a
 * different party — which is the whole reason they are not concatenated here.
 * The renderer stacks them; a test can pin any one of them without a DOM.
 */
export type PhaseTooltip = {
  /** The state word the badge already shows. Bare and lowercase, like the badge. */
  headline: string;
  /** What that phase means in plain English. Always present. */
  meaning: string;
  /** Why nothing is advancing — present only when the agent reported `blocked`. */
  stall: string | null;
  /** The agent's OWN words, verbatim. Never paraphrased into house voice. */
  note: string | null;
  /** The tracker's claim, attributed to the tracker. `null` on a phase with no ticket. */
  tracker: string | null;
  /** Every claim above as one string, for `aria-label`. */
  aria: string;
};

/**
 * A phase mark's hover: what the badge shows, what it means, and — where there
 * is a ticket — what the TRACKER separately says about the same object.
 *
 * THE TWO AXES ARE KEPT APART BY ATTRIBUTION, not by wording. Every sentence
 * names its claimant: "The agent …" for the phase half, "<Tracker> says …" for
 * the tracker half. That is what makes `closed` beside `scoping` read as two
 * true statements — the issue was closed, and the agent is still working out the
 * job — rather than as a contradiction the reader has to resolve. Merging them
 * into one sentence is precisely the conflation this tooltip exists to undo.
 *
 * DEGRADES TO THE PHASE HALF ALONE, because a fleet card's phase belongs to a
 * workspace and there is no ticket to ask. The ticket parameter is optional for
 * that call site, not for future-proofing.
 *
 * `blocked` here is a claim about the WORK — the agent cannot finish this task,
 * and that survives the agent dying or being respawned. It is NOT the activity
 * axis's `blocked` ("stopped at a prompt, waiting for you right now", which
 * `glossary.tsx` defines as `agent_blocked` and which clears when you answer).
 * Nothing in this copy says "waiting for you" for that reason.
 *
 * There is NO recency claim anywhere in it. A per-ticket row carries no
 * timestamp, so "still", "since" and "as of" would all be inventions.
 */
export function phaseTooltip(mark: PhaseMark, ticket?: PhaseTooltipTicket | null): PhaseTooltip {
  // The badge's own words, so the hover starts where the reader's eye already
  // is. `blocked in <phase>` keeps the position a bare `blocked` would drop.
  const headline = mark.blocked ? `blocked in ${mark.phase}` : mark.phase;
  const subject = ticket ? `this ${ticketKindLabel(ticket.kind)}` : "this task";
  const note = mark.note?.trim() || null;
  const tracker = ticket ? trackerClaim(ticket) : null;

  return {
    headline,
    meaning: PHASE_MEANING[mark.phase],
    stall: mark.blocked
      ? `The agent cannot finish ${subject}, so it stays at this step until someone clears the block.`
      : null,
    note,
    tracker,
    // At least everything the native `title` and the old `aria-label` carried
    // between them, plus the tracker's claim — a screen-reader user gets the
    // same two axes a sighted reader does, in the same order.
    aria: `task phase: ${headline}${note ? ` — ${note}` : ""}, step ${mark.index + 1} of ${mark.total}${tracker ? `. ${tracker}` : ""}`,
  };
}

/**
 * `null` when the tracker has said nothing at all — a ref can be attached before
 * its tracker was ever reachable, and a row with no status badge must not gain a
 * sentence claiming one.
 */
function trackerClaim(ticket: PhaseTooltipTicket): string | null {
  const raw = ticket.status?.trim();
  const said = TRACKER_WORD[ticketState(ticket.status)] ?? (raw ? `“${raw}”` : null);
  if (!said) return null;
  return `${providerLabel(ticket.provider)} says this ${ticketKindLabel(ticket.kind)} is ${said}.`;
}

/** One run of a ticket title: literal prose, or something that was in backticks. */
export type TitleRun = { text: string; code: boolean };

/**
 * Split a ticket title on backtick pairs, and do NOTHING else.
 *
 * WHY THIS IS NOT THE VENDORED MARKDOWN RENDERER, which is what a title like
 * ``fix `state.branch` when HEAD`` obviously wants. `MarkdownTextPrimitive`
 * takes no text at all — it calls `useMessagePartText()` and reads from
 * assistant-ui's message context, so it can only ever render the message it is
 * mounted inside. There is no prop to hand it a string, which makes it not
 * "hard to constrain to inline" but structurally inapplicable here.
 *
 * So this is the sanctioned fallback: code spans, nothing else. It is
 * deliberately NOT a markdown parser — no emphasis, no links, no lists — because
 * the failure this must never have is a title emitting a block element into a
 * table row, and the surest way to never emit one is to have no rule that can.
 *
 * It also strips nothing. Emoji, `*`, `_`, `#` and every other character
 * survive verbatim, because a title is somebody else's text and quietly
 * deleting characters from it is the provider-boundary mistake of correcting
 * data instead of presenting it. An unpaired backtick is therefore literal: a
 * lone tick is far more likely to be someone's apostrophe-adjacent typo than an
 * unclosed span, and rendering the rest of the line as code would be a louder
 * error than showing the tick.
 */
export function titleRuns(title: string): TitleRun[] {
  const runs: TitleRun[] = [];
  // Pairs only: the capture is what makes an odd trailing tick fall through as
  // literal text rather than opening a span that never closes.
  const pattern = /`([^`]+)`/g;
  let cursor = 0;
  for (const match of title.matchAll(pattern)) {
    const at = match.index;
    if (at > cursor) runs.push({ text: title.slice(cursor, at), code: false });
    runs.push({ text: match[1]!, code: true });
    cursor = at + match[0].length;
  }
  if (cursor < title.length) runs.push({ text: title.slice(cursor), code: false });
  return runs;
}

/**
 * The stored ref, refreshed by whatever the tracker just said.
 *
 * FIELD-BY-FIELD with the cached value as the floor, never a wholesale
 * replacement: a resolve that comes back thin must not blank a title the
 * workspace has been showing since it was created. Absence of a live read
 * (`undefined`) leaves the cached ref exactly as it is, which is what makes a
 * failed resolve a non-event for the reader.
 *
 * `ambiguous` is the one field the tracker may NOT overwrite. It records that
 * Grove inferred this association from a branch that more than one provider or
 * key claimed — a fact about the LINK, not about the ticket — and the resolve
 * route reports every ticket it fetched by id as unambiguous. Taking its answer
 * would silently clear the very uncertainty the user is being asked to resolve.
 */
export function mergeTicket(cached: TicketRef, live: TicketRef | undefined): TicketRef {
  if (!live) return cached;
  return {
    ...cached,
    ...live,
    title: live.title ?? cached.title,
    url: live.url ?? cached.url,
    status: live.status ?? cached.status,
    assignee: live.assignee ?? cached.assignee,
    ambiguous: cached.ambiguous,
  };
}

/**
 * A session row's label. A row with no attributed title and no prompts falls
 * back to the bare id, because rendering "untitled session" would hide WHICH of
 * several otherwise-identical rows you are looking at.
 */
export function sessionLabel(session: SessionSummaryView): string {
  return session.title ?? session.first_prompt ?? session.git_branch ?? session.session_id;
}

/**
 * The daemon captures panes with `tmux capture-pane -e`, so the payload is a
 * fixed character grid carrying SGR colour escapes — and those escapes ARE the
 * signal. An agent TUI says "this test failed" in red and "applied" in green;
 * stripping the escapes throws that away and leaves a wall of one-colour text.
 *
 * `FancyAnsi` HTML-escapes the payload and injects nothing but `<span style>`
 * colour runs, so the rendered node's `textContent` is still exactly the plain
 * stripped grid — which is what a screen reader and a test read. Cursor-motion
 * and screen-erase sequences are dropped rather than rendered, because the
 * capture is a settled grid with no motion to replay.
 *
 * Trailing blank rows go: tmux pads the capture to the pane's full height, and
 * keeping the padding scrolls the reader through an empty screen to reach it.
 */
export function paneHtml(ansi: string | null | undefined): string | null {
  const trimmed = ansi?.replace(/\s+$/, "");
  return trimmed ? FANCY.toHtml(trimmed) : null;
}

/** Stateless converter — one instance is reused for every frame. */
const FANCY = new FancyAnsi();

function formatTime(iso: string): string {
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleString();
}

/**
 * The syntax-highlighter language for a tool call's REQUEST field, or
 * `undefined` when this field is not one carrying literal code text.
 *
 * Scoped to a field NAME as well as a tool name: a shell-shaped call's other
 * arguments (`description`, `timeout`) are prose or numbers, not code, and
 * highlighting them as shell would rank them the same as the command itself.
 * `command` covers Claude's `Bash` and Codex's `exec_command`/
 * `local_shell_call`. `input` is Codex's `custom_tool_call` wire shape — its
 * `input` field IS the raw tool body verbatim, unlike `function_call`'s JSON
 * `arguments` (`grove.core.agents.codex.py::_Line.tool_input`,
 * `tool_input = {"input": self.custom_tool_input()}`) — so for the one named
 * `exec` (Codex's JavaScript code-mode tool) that field is the script.
 *
 * Anything else renders exactly as it always has, plain text: guessing a
 * language for a call this function does not recognise is worse than
 * highlighting none.
 */
const SHELL_TOOL_NAMES = new Set(["bash", "exec_command", "local_shell_call"]);
const CODE_MODE_TOOL_NAMES = new Set(["exec"]);
const COMMAND_FIELD_NAMES = new Set(["command", "input"]);

export function toolCommandLanguage(toolName: string, fieldName: string): "bash" | "javascript" | undefined {
  if (!COMMAND_FIELD_NAMES.has(fieldName)) return undefined;
  const name = toolName.toLowerCase();
  if (SHELL_TOOL_NAMES.has(name)) return "bash";
  if (CODE_MODE_TOOL_NAMES.has(name)) return "javascript";
  return undefined;
}
