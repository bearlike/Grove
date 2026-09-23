import { FancyAnsi } from "fancy-ansi";
import {
  ArrowDownToLineIcon,
  ArrowUpFromLineIcon,
  CircleCheckIcon,
  CircleDashedIcon,
  CircleDotIcon,
  CodeXmlIcon,
  DatabaseIcon,
  FilePenLineIcon,
  FileTextIcon,
  GitMergeIcon,
  GitPullRequestClosedIcon,
  GitPullRequestDraftIcon,
  GitPullRequestIcon,
  MessageSquareIcon,
  MinusIcon,
  PlusIcon,
  type LucideIcon,
} from "lucide-react";

import { abbreviate } from "@/components/grove/usage/format";

import type {
  CommitSummaryView,
  DurationView,
  GenerationLatencyView,
  NativeFactsView,
  PhaseView,
  SessionSummaryView,
  TicketRef,
  WorkspaceActivityView,
  WorkspacePeekView,
  WorkspaceStateView,
} from "@/lib/grove/api";
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
 * What the Info and Changes tabs actually READ off a workspace record — and
 * therefore the most either of them may require of a caller.
 *
 * A `Pick`, not the whole `WorkspaceStateView`, and the narrowing is
 * load-bearing rather than tidy. TypeScript is structural, so a component
 * asking for exactly the fields it uses is satisfied by *any* object carrying
 * them: the authenticated peek, and equally the deliberately smaller payload the
 * public share view sends (`PublicWorkspaceStateView`, an allowlist that holds
 * no host path). That is what lets one Info tab and one Changes tab serve both
 * surfaces with no fork, no branch and no second copy to keep in step.
 *
 * The precedent is already here: `adapters/branch.ts::baseBranchOf` has always
 * taken `Pick<WorkspaceStateView, "branch" | "base_branch">`. This is the same
 * move applied to the two tabs that consume it.
 *
 * Deliberately ABSENT, because neither tab reads them and a public reader must
 * never receive them: `repo_root`, `worktree_path`, `tmux_session`, `container`,
 * `share_token`. Adding one here to satisfy a new call site is the moment to
 * ask whether that call site belongs on a shared surface at all.
 */
export type WorkspaceIdentity = Pick<
  WorkspaceStateView,
  | "id"
  | "title"
  | "description"
  | "status"
  | "branch"
  | "base_branch"
  | "base_commit"
  | "agent_name"
  | "created_at"
  | "updated_at"
  | "paused_at"
  | "placement"
  | "runtime"
  | "runtime_fallback_reason"
  | "runtime_default_config"
  | "ticket_refs"
>;

/**
 * The working-tree read those tabs need: the identity above plus the five
 * counters. `WorkspacePeekView` minus the pane — which is exactly what the
 * public payload is, and exactly what a reader with no terminal access may see.
 */
export type WorkspaceRead = Pick<
  WorkspacePeekView,
  "base_ahead" | "base_behind" | "diff_added" | "diff_removed" | "dirty_files"
> & { state: WorkspaceIdentity };

/**
 * The activity fields the shared selectors below reduce over.
 *
 * Never the whole `WorkspaceActivityView` — that shape embeds a full
 * `WorkspaceStateView`, so requiring it would drag every host path back into the
 * one payload built to exclude them.
 *
 * `todo` is OPTIONAL where `sessions` and `phase` are required, and the split is
 * a real one rather than an accident of who sends what. The task phase is a
 * claim about the work and belongs on any surface showing that work; the todo
 * COUNTS ride the ~1 Hz fleet delta, which the public view does not subscribe
 * to. Absent therefore means "this surface has no counts", which renders as no
 * badge — never as `0/0`.
 */
export type ActivityRead = Pick<WorkspaceActivityView, "sessions" | "phase"> & {
  todo?: WorkspaceActivityView["todo"];
};

/**
 * Which work-panel surfaces exist, IN THE ORDER THE STRIP RENDERS THEM — the
 * one census, from which the type is derived rather than written twice.
 *
 * There were three copies of this list a moment ago: this union, a storage
 * validation array, and `work-panel.tsx`'s own label/icon tuples.
 * Three places to keep in sync is how a sixth tab ends up unreachable from the
 * restore path while still rendering, which is invisible until someone reloads
 * onto it. `work-panel.tsx` now maps over this array and looks its label and
 * icon up in a `Record<PanelTab, …>`, so a tab added here without a label
 * fails to compile instead of silently vanishing from the strip.
 */
export const PANEL_TAB_VALUES = [
  "terminal",
  "changes",
  "diagram",
  "files",
  "info",
  "controls",
] as const;

/** The built-in work-panel surfaces. */
export type BuiltInPanelTab = (typeof PANEL_TAB_VALUES)[number];

/** Which work-panel surface is showing. */
export type PanelTab = BuiltInPanelTab | `panel:${string}`;

/** Keep daemon-provided panel names disjoint from the built-in tab census. */
export function panelTabValue(name: string): PanelTab {
  return `panel:${name}`;
}

/**
 * The tabs a reader with no Grove session may see: what the work IS, and what
 * it CHANGED. Terminal, Files, Controls and Diagram are absent because each is
 * either a live handle on the machine or a way to change it.
 *
 * A subset of `PANEL_TAB_VALUES` rather than a parallel list, so a name that
 * does not exist in the census cannot be written here. Withholding a tab is
 * CHROME, never the boundary: the public surface is safe because the daemon
 * serves it read-only routes carrying no diagram at all.
 */
export const SHARED_TABS: readonly BuiltInPanelTab[] = ["changes", "info"];

/**
 * Which built-in tabs this render offers, in the census's order.
 *
 * Diagram is CONDITIONAL where the other five are not: it exists only while the
 * workspace carries a diagram descriptor, so an ordinary workspace's strip is
 * unchanged. A `hasDiagram` flag rather than the descriptor itself, because the
 * question this answers is presence, and passing the record would invite a
 * second reading of `mode` here that disagrees with the tab's own.
 */
export function offeredPanelTabs(
  privileged: boolean,
  hasDiagram: boolean,
): readonly BuiltInPanelTab[] {
  return PANEL_TAB_VALUES.filter(
    (value) =>
      (privileged || SHARED_TABS.includes(value)) &&
      (value !== "diagram" || (privileged && hasDiagram)),
  );
}

/** The workspace's two panes; `split` shows both at once. */
export type PaneView = "transcript" | "work" | "split";

/**
 * The page's default surface before a reader has chosen one.
 *
 * An unborn transcript has nothing to read, so the page opens on the work pane
 * alone; once a turn exists, the conversation and its work belong beside each
 * other. This is a DEFAULT only: `Workspace` keeps an explicit null choice
 * until a reader picks either control, so an async turn arriving later never
 * takes a manually selected pane or tab away.
 */
export interface WorkspaceSelection {
  /** `null` means neither this visit nor a saved choice selected a pane. */
  view: PaneView | null;
  /** `null` means neither this visit nor a saved choice selected a work tab. */
  workTab: PanelTab | null;
}

/**
 * Resolve automatic defaults only for the parts a reader has not chosen.
 *
 * The fields resolve independently: selecting Transcript does not claim that
 * its reader also chose a work tab. More importantly, a selection stays fixed
 * across `false` → `true` as the first transcript turn arrives.
 */
export function resolvedWorkspaceSelection(
  hasTranscript: boolean,
  selection: WorkspaceSelection,
): { view: PaneView; workTab: PanelTab } {
  return {
    view: selection.view ?? (hasTranscript ? "split" : "work"),
    // EVERY visit lands on Info, and the work tab is deliberately NOT persisted
    // (see `Workspace`) — the question a reader has on arriving at a workspace
    // is what it is, not what its terminal was doing three days ago. Info is
    // unconditional in the census and offered to both audiences, so this
    // default is always a tab that exists; `WorkPanel` still falls back for a
    // *selected* tab that stops being offered.
    workTab: selection.workTab ?? "info",
  };
}

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

const PANE_VIEWS: readonly PaneView[] = ["transcript", "work", "split"];

/**
 * A valid persisted pane selection, or no selection at all.
 *
 * `null` is intentionally distinct from a default pane. A missing, stale, or
 * hand-edited value means the workspace has not been chosen yet, so the live
 * transcript rule may decide it. A valid value is a reader's old choice and
 * must survive an asynchronous transcript arrival unchanged.
 */
export function storedView(stored: unknown): PaneView | null {
  return (PANE_VIEWS as readonly unknown[]).includes(stored)
    ? (stored as PaneView)
    : null;
}

/*
 * There is deliberately no `storedWorkTab`. The work tab is the one piece of
 * this surface's UI state that is NOT persisted: every visit lands on Info (see
 * `resolvedWorkspaceSelection`), so a reader restored onto a days-old Terminal
 * would be the bug. A validator with no reader is how a dead key comes back.
 */

export type LifecycleAction = "pause" | "resume" | "respawn" | "kill";

/** Native interruption is a live capability, not an inference from transcript activity. */
export function canInterruptNative(
  state: Pick<WorkspaceStateView, "native" | "status">,
): boolean {
  return state.native && ["running", "active", "idle"].includes(state.status);
}

/**
 * The lifecycle verbs worth offering for a status.
 *
 * A pure UX mirror of the engine's own gate, never a re-implementation of it:
 * a stale snapshot offering an illegal verb just surfaces the daemon's typed
 * refusal. Native and root-placement workspaces cannot suspend. A live host
 * native session permits explicit recovery even if its worker has not exited;
 * that is distinct from automatic revival after an observed exit.
 */
export function availableActions(
  state: Pick<WorkspaceStateView, "status" | "placement" | "native" | "runtime">,
): readonly LifecycleAction[] {
  const suspendable = state.placement !== "root" && !state.native;
  switch (state.status) {
    case "active":
    case "running":
    case "idle":
      if (state.native && state.runtime === "host") return ["respawn", "kill"];
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
export function defaultDeleteBranch(
  state: Pick<WorkspaceStateView, "placement" | "branch_provenance">,
): boolean {
  if (state.placement === "root") return false;
  return state.branch_provenance === "grove";
}

/**
 * ONE BOUNDED FACT — the shape `CardCell` renders, wherever a surface has a
 * grid of measurements rather than a term-and-value list.
 *
 * `value: null` is the only way a cell says NOT MEASURED, and it is deliberately
 * distinct from `"0"`: an absent figure was never reported, a zero was reported
 * and is zero. Both render; only one of them is a figure.
 *
 * Shared between the Info tab's Activity card and the Changes tab's Divergence
 * card because they are the same object at two ranges — what this session did,
 * and what this branch did. A second shape would be a second metric system, and
 * one of them would drift.
 */
export type MetricFact = {
  /** Stable across renders and across a card's conditional fact sets. */
  key: string;
  label: string;
  icon: LucideIcon;
  /** The formatted figure, or `null` when nothing reported it. */
  value: string | null;
  /** Grove computed or summed this figure rather than reading a provider's. */
  derived: boolean;
  /** What it was computed from, and — where abbreviated — its exact value. */
  title: string;
  /** A glossary definition for the LABEL, where the plain words mislead. */
  term?: GlossaryTerm;
  /** The figure's semantic tone. Absent means neutral, which is most of them. */
  tone?: MetricTone;
};

/**
 * The tones a figure may take, and the one rule that governs them: **colour is
 * never the carrier.** Every toned figure here also prints a sign or a unit in
 * its label, so the cell survives greyscale (§4.7).
 *
 * A ZERO IS NEVER TONED, which is `figureTone`'s rule on the fleet card applied
 * here: `+0` in green claims something happened. The selector decides that,
 * because only it holds the number.
 */
export type MetricTone = "added" | "removed" | "pending";

/** One bounded fact on the Activity card, keyed by the census below. */
export type ActivityFact = MetricFact & { key: ActivityFactKey };

export type ActivityFactKey =
  | "turns"
  | "tool_calls"
  | "output"
  | "input_total"
  | "fresh_input"
  | "cache_read"
  | "cache_write";

/**
 * The session's activity as bounded facts, in reading order.
 *
 * SIX FACTS OR FOUR, NEVER SEVEN. `AgentActivityView.tokens_in` folds fresh
 * input, cache read and cache creation together BY DESIGN (see its engine
 * docstring), so where the wire carries the breakdown the fold is dropped and
 * its three classes take its place — a folded total beside its own unfolding
 * restates one magnitude twice, once as a mystery and once explained. Where the
 * wire carries no breakdown (an older daemon, or a row `ActivityService` never
 * resolved a message spine for) the total stands alone and no class is
 * synthesised: an unmeasured class is not a zero.
 *
 * Token counts are abbreviated because their magnitude is the signal — nobody
 * reads the units digit of 1,284,193 — and §3 requires the exact figure to
 * survive one hover away, which is what every `title` here carries.
 *
 * `reasoning` and `provider_total` are deliberately absent. They answer a
 * different question (a provider's informational split, its own stated total)
 * than "why is tokens-in this big", which the three classes summing to it
 * already answer completely.
 *
 * SCOPE IS `sessions[0]` — the same row `sessionClocks` and `sessionLatency`
 * read, so the whole tab describes one session — and the card labels that scope
 * rather than implying a sum over every sub-agent the workspace ever ran.
 */
/**
 * Whether the agent is working right now — the fact a live cue is allowed to
 * move for.
 *
 * `sessions[0]`, the same row every other selector on this tab reads, so the
 * card describes one session throughout. **This is a READ of the engine's
 * answer, never a second derivation of it**: the engine promotes a session
 * whose sidechain fleet is active to `working` even when the orchestrator's own
 * turn has closed, so `active_subagents` must not be consulted beside this —
 * `working-loader.tsx` records why. `lib/grove/runtime/thread.ts` asks the same
 * question of the dashboard snapshot because that is the shape it holds; both
 * read `activity.state === "working"` and neither decides anything more.
 *
 * Absent activity is NOT working: a workspace nobody has reported on has not
 * claimed to be busy, and a cue that pulses on silence says the opposite.
 */
export function agentIsWorking(activity: ActivityRead | null): boolean {
  return activity?.sessions[0]?.activity.state === "working";
}

/**
 * The primary native session's recorded exit reason, if it has ended.
 *
 * `current_task` is the engine's recorded reason only in the error state. A
 * stale reason beside a subsequent working session must not announce a death
 * that the engine has already recovered from.
 */
export function agentExited(activity: ActivityRead | null): string | null {
  const live = activity?.sessions[0]?.activity;
  const reason = live?.current_task?.trim();
  return live?.state === "error" && reason ? reason : null;
}

export function activityFacts(activity: ActivityRead | null): ActivityFact[] | null {
  const session = activity?.sessions[0];
  const live = session?.activity;
  if (!live) return null;

  const facts: ActivityFact[] = [
    {
      key: "turns",
      label: "Turns",
      icon: MessageSquareIcon,
      value: COUNT.format(live.human_turns),
      derived: false,
      title: `${COUNT.format(live.human_turns)} human turns recorded in this session.`,
    },
    {
      key: "tool_calls",
      label: "Tool calls",
      icon: CodeXmlIcon,
      value: COUNT.format(live.tool_calls),
      derived: false,
      title: `${COUNT.format(live.tool_calls)} tool invocations recorded in this session.`,
    },
    {
      key: "output",
      label: "Output tokens",
      icon: ArrowUpFromLineIcon,
      value: COMPACT.format(live.tokens_out),
      derived: false,
      title: `${COUNT.format(live.tokens_out)} output tokens, as reported for this session.`,
    },
  ];

  const tokens = session?.tokens;
  if (!tokens) {
    facts.push({
      key: "input_total",
      label: "Input tokens",
      icon: ArrowDownToLineIcon,
      value: COMPACT.format(live.tokens_in),
      derived: false,
      title: `${COUNT.format(live.tokens_in)} input tokens — fresh input, cache reads and cache writes folded together, which this session did not report separately.`,
    });
    return facts;
  }

  facts.push(
    tokenFact("fresh_input", "Fresh input", ArrowDownToLineIcon, tokens.fresh_input),
    {
      ...tokenFact("cache_read", "Cache read", DatabaseIcon, tokens.cache_read),
      term: "cache_read_tokens",
    },
    {
      ...tokenFact("cache_write", "Cache write", FileTextIcon, tokens.cache_creation),
      term: "cache_creation_tokens",
    },
  );
  return facts;
}

/**
 * One input class, summed by Grove and marked as such.
 *
 * DERIVED, because it is: the provider reports a class per message and Grove
 * adds them up across the session. Calling that a provider total would claim an
 * authority the number does not have — and marking every dynamic figure derived
 * would carry no information at all, which is why turns, tool calls and the
 * session's own output count are not marked.
 */
function tokenFact(
  key: ActivityFactKey,
  label: string,
  icon: LucideIcon,
  count: number | null | undefined,
): ActivityFact {
  return {
    key,
    label,
    icon,
    value: count == null ? null : COMPACT.format(count),
    derived: count != null,
    title:
      count == null
        ? `This session reported no ${label.toLowerCase()} count.`
        : `${COUNT.format(count)} tokens — Grove's sum of the per-message ${label.toLowerCase()} counts this session's provider reported.`,
  };
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
export function sessionClocks(
  activity: ActivityRead | null,
): DurationView | null {
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
  activity: ActivityRead | null,
): GenerationLatencyView | null {
  return activity?.sessions[0]?.latency ?? null;
}

/**
 * The owned stream's own facts for `sessions[0]`, or `null` for a terminal
 * session and for an older daemon that never sends the field — the same
 * `!= null` rule the context meter follows, since both absences mean "nobody
 * said" and neither is a zero.
 */
export function nativeFacts(activity: ActivityRead | null): NativeFactsView | null {
  return activity?.sessions[0]?.activity.native ?? null;
}

/**
 * What this branch has done, as the same bounded facts the Activity card uses.
 *
 * THREE FACTS OR FIVE, AND THE TWO EXTRA ARE WITHHELD RATHER THAN ZEROED.
 * `base_ahead`/`base_behind` are the only figures the daemon derives from the
 * base NAME — deliberately, because "behind" asks how far the base has moved
 * and a frozen commit can only ever answer zero — so with no separate base the
 * range is `HEAD..branch`, structurally empty however much work has been done.
 * **A zero that cannot be anything else is not a measurement.** The other three
 * are anchored on `base_commit` and stay true either way.
 *
 * THE UNITS ARE IN THE LABELS BECAUSE THE SCOPES DIFFER AND NOTHING ELSE SAYS
 * SO. `dirty_files` counts uncommitted paths in the worktree right now;
 * `diff_added`/`diff_removed` count lines on the branch since its diff base,
 * which is NOT the agent's cumulative edits; ahead/behind count commits against
 * the base branch, not against a remote upstream. Three different questions that
 * would otherwise read as one row of numbers — the same reason the fleet card's
 * labels spell them out, and the `title` sentences here are that card's,
 * verbatim, so the two surfaces teach one vocabulary.
 *
 * `abbreviate`, NOT this file's `COMPACT`: the rail already renders these exact
 * five numbers that way, and one figure rendered two ways across two surfaces is
 * the drift a shared metric system exists to prevent.
 */
export function divergenceFacts(
  peek: Pick<
    WorkspaceRead,
    "base_ahead" | "base_behind" | "diff_added" | "diff_removed" | "dirty_files"
  >,
  hasBase: boolean,
): MetricFact[] {
  const facts: MetricFact[] = [];
  if (hasBase) {
    facts.push(
      gitFact("ahead", "Commits ahead", ArrowUpFromLineIcon, peek.base_ahead, {
        detail: "commits ahead of the base branch (not unpushed commits)",
        tone: "added",
      }),
      gitFact("behind", "Commits behind", ArrowDownToLineIcon, peek.base_behind, {
        detail: "commits behind the base branch",
        tone: "pending",
      }),
    );
  }
  facts.push(
    gitFact("dirty", "Dirty files", FilePenLineIcon, peek.dirty_files, {
      detail: "uncommitted files in the worktree",
      tone: "pending",
    }),
    gitFact("added", "Lines added", PlusIcon, peek.diff_added, {
      detail: "lines added on the branch since its diff base",
      tone: "added",
      sign: "+",
    }),
    gitFact("removed", "Lines removed", MinusIcon, peek.diff_removed, {
      detail: "lines removed on the branch since its diff base",
      tone: "removed",
      sign: "−",
    }),
  );
  return facts;
}

/**
 * One git counter. The sign rides the FIGURE rather than arriving as a second
 * glyph, so `+12` reads as one value — and it is what makes the tone redundant
 * rather than load-bearing.
 *
 * A ZERO IS QUIET. `+0` in green would claim a change that did not happen, which
 * is the same fabricated-signal mistake as a fabricated zero one step on.
 */
function gitFact(
  key: string,
  label: string,
  icon: LucideIcon,
  count: number,
  options: { detail: string; tone: MetricTone; sign?: string },
): MetricFact {
  return {
    key,
    label,
    icon,
    value: `${options.sign ?? ""}${abbreviate(count)}`,
    derived: false,
    title: `${COUNT.format(count)} ${options.detail}`,
    tone: count === 0 ? undefined : options.tone,
  };
}

const COUNT = new Intl.NumberFormat("en-US");
const COMPACT = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 1,
});

/** Commits as the vendored `Timeline`'s event shape; the newest commit is "now". */
export function commitEvents(
  commits: readonly CommitSummaryView[],
): TimelineEvent[] {
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
export function ticketPhaseKey(
  ref: Pick<TicketRef, "provider" | "id">,
): string {
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
    phase.tickets.map(
      (claim) => [claim.ticket, { ...claim, total: phase.total }] as const,
    ),
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
  /** A count for every phase, kept separate from the orthogonal flags. */
  phases: Record<TaskPhase, number>;
  /** 0–1 mean completion across every attached ticket, unclaimed ones included. */
  fraction: number;
};

const TASK_PHASES: readonly TaskPhase[] = [
  "scope",
  "plan",
  "build",
  "verify",
  "deliver",
  "handoff",
];

function emptyPhaseCounts(): Record<TaskPhase, number> {
  return Object.fromEntries(TASK_PHASES.map((phase) => [phase, 0])) as Record<TaskPhase, number>;
}

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
  const phases = emptyPhaseCounts();

  for (const ref of refs) {
    const claim = claims.get(ticketPhaseKey(ref));
    if (!claim) continue;
    reported += 1;
    phases[claim.phase] += 1;
    if (claim.blocked) blocked += 1;
    if (claim.phase === "handoff") done += 1;
    // `index / (total - 1)` so `handoff` is exactly 1 and `scope` exactly 0 —
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
    phases,
    fraction: progress / refs.length,
  };
}

/**
 * What the average-phase bar says about its own coverage, under the bar.
 *
 * **THE COMPLETION COUNT AND THE PHASE AVERAGE ARE BOTH TRUE AT ONCE, and this
 * line is what stops a reader collapsing them.** Two tickets both at
 * `deliver` — index 4 of six phases — average 80% phase progress while
 * `0 / 2 done` is equally correct, because one is a position along the work and
 * the other is a count of finished work. Replacing the 80% with 0% would be a
 * different, worse number: it would throw away every measurement the agents
 * actually reported.
 *
 * Coverage is stated in the direction that matters. Where every ref carries a
 * claim the line says so; where some do not, it names the SILENCE rather than
 * the measurement, because `ticketRollup` scores an unclaimed ref as zero and a
 * reader has no other way to tell an understated average from a real one.
 */
export function rollupCoverage(rollup: TicketRollup): string {
  const parts = [`${rollup.done} / ${rollup.total} done`];
  parts.push(
    rollup.unreported > 0
      ? `${rollup.unreported} claim${rollup.unreported === 1 ? "" : "s"} not reported`
      : `${rollup.reported} claim${rollup.reported === 1 ? "" : "s"} reported`,
  );
  if (rollup.blocked > 0) parts.push(`${rollup.blocked} blocked`);
  return parts.join(" · ");
}

/**
 * The arithmetic behind the percentage, as §3's provenance `title` — this figure
 * is Grove's, not a tracker's, and a reader reconciling it needs the formula
 * rather than a claim that it is derived.
 */
export function rollupFormula(rollup: TicketRollup): string {
  return `Grove's mean of index / (total − 1) across all ${rollup.total} attached ${rollup.total === 1 ? "ref" : "refs"}; a ref with no claim counts as 0. Phase progress, not completion.`;
}

/**
 * Rank attached tickets in the same order as
 * `core/contracts/ticket_order.py::ticket_sort_key`: PRs, then issues; live
 * tracker state; the furthest non-terminal claim; and finally the numeric or
 * lexical id. A claim is deliberately passed in rather than fetched here — it
 * is already on the Info tab, so ordering costs no request or loading state.
 */
export function ticketSortKey(
  ticket: Pick<TicketRef, "kind" | "status" | "id"> & { draft?: boolean },
  phase: Pick<TicketPhaseMark, "index" | "phase"> | null,
): [number, number, number, number] {
  const state = ticketState(ticket.status, ticket.draft, ticket.kind);
  const stateRank: Record<TicketState, number> = {
    open: 0,
    draft: 1,
    unknown: 2,
    merged: 3,
    closed: 3,
  };

  return [
    ticket.kind === "pull_request" ? 0 : 1,
    stateRank[state],
    phase === null ? 2 : phase.phase === "handoff" ? 1 : 0,
    phase === null || phase.phase === "handoff" ? 0 : -phase.index,
  ];
}

/** Compare two joined ticket rows by the shared cross-client ordering contract. */
export function compareTicketRefs(
  a: Pick<TicketRef, "kind" | "status" | "id"> & { draft?: boolean },
  aPhase: Pick<TicketPhaseMark, "index" | "phase"> | null,
  b: Pick<TicketRef, "kind" | "status" | "id"> & { draft?: boolean },
  bPhase: Pick<TicketPhaseMark, "index" | "phase"> | null,
): number {
  const aKey = ticketSortKey(a, aPhase);
  const bKey = ticketSortKey(b, bPhase);
  for (let index = 0; index < aKey.length; index += 1) {
    if (aKey[index] < bKey[index]) return -1;
    if (aKey[index] > bKey[index]) return 1;
  }

  const aIsNumeric = /^-?\d+$/.test(a.id);
  const bIsNumeric = /^-?\d+$/.test(b.id);
  if (aIsNumeric && bIsNumeric) {
    const [aId, bId] = [BigInt(a.id), BigInt(b.id)];
    return aId < bId ? -1 : aId > bId ? 1 : 0;
  }
  if (aIsNumeric !== bIsNumeric) return aIsNumeric ? -1 : 1;
  return a.id.localeCompare(b.id);
}

/** Sort direct ticket refs, which have no per-ticket claims yet. */
export function sortTicketRefs(refs: readonly TicketRef[]): TicketRef[] {
  return [...refs].sort((a, b) => compareTicketRefs(a, null, b, null));
}

type TicketProvider = TicketRef["provider"];
type TicketKind = TicketRef["kind"];

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

/**
 * `kind` NARROWS ONE CELL AND NOTHING ELSE: an issue is never `merged`.
 *
 * MERGED WINS OVER CLOSED wherever both could apply, because a merged pull
 * request is also a closed one and "closed" is the weaker of the two true
 * statements — `STATE_BY_WORD` gets there by reading the tracker's own word, so
 * the precedence is the tracker's rather than an inference of ours. What must
 * never be inferred is the other direction: a forge numbers issues and pull
 * requests in one space, so a mis-typed or mid-correction ref can arrive as an
 * issue carrying a pull request's word, and drawing that issue purple would
 * assert a branch was landed. It degrades to `closed` — the truest thing left —
 * which is the same answer `TICKET_GLYPH`'s `issue.merged` cell already gives.
 *
 * Optional, because two of the four call sites genuinely do not know the kind: a
 * status tone is a property of the word alone, and a phase tooltip quotes the
 * tracker rather than colouring anything. Omitting it can only ever leave the
 * old, wider reading.
 */
export function ticketState(
  status: string | null | undefined,
  draft = false,
  kind?: TicketKind,
): TicketState {
  if (draft) return "draft";
  if (!status) return "unknown";
  const state = STATE_BY_WORD[status.trim().toLowerCase()] ?? "unknown";
  return state === "merged" && kind === "issue" ? "closed" : state;
}

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
 * Grove's word for a normalized state — and the TRACKER'S own word where Grove
 * could not normalize it.
 *
 * §4.7's second carrier: the state hue only ever agrees with a word that is
 * always printed, so `open` green, `closed` red and `merged` purple all survive
 * greyscale. `unknown` has no controlled word to assert, so it quotes whatever
 * the tracker actually wrote rather than inventing one — marked, not
 * interpreted, exactly as `TicketState`'s own docstring requires. A tracker that
 * has said nothing at all yields `null` and the row prints no state.
 */
const STATE_LABEL: Record<TicketState, string | null> = {
  open: "Open",
  closed: "Closed",
  merged: "Merged",
  draft: "Draft",
  unknown: null,
};

export function ticketStateLabel(state: TicketState, status?: string | null): string | null {
  return STATE_LABEL[state] ?? status?.trim() ?? null;
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
  scope:
    "The agent is reading the ticket, the code and the tests, working out what the job actually is.",
  plan:
    "The agent understands the problem and is choosing an approach or writing it down.",
  build: "The agent is editing files.",
  verify:
    "The agent is running tests, linters or the build, and reading its own diff back.",
  deliver:
    "The agent is committing, pushing, opening or updating the pull request, writing the handoff.",
  handoff:
    "The agent has transferred the finished work to the user in the form they asked for — nothing is left for the agent to do here.",
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
export type PhaseTooltipTicket = Pick<TicketRef, "provider" | "kind" | "status"> & {
  /** Older daemon responses omit this additive enrichment field. */
  draft?: boolean;
};

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
 * the tracker half. That is what makes `closed` beside `scope` read as two
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
 * The deliberate cut is title, URL, assignee and link uncertainty: all four are
 * already visible on the row or its destination, and repeating them buries the
 * agent's note — the one fact the tracker cannot supply. There is NO recency
 * claim anywhere in it. A per-ticket row carries no timestamp, so "still",
 * "since" and "as of" would all be inventions.
 */
export function phaseTooltip(
  mark: PhaseMark,
  ticket?: PhaseTooltipTicket | null,
): PhaseTooltip {
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
  const said =
    TRACKER_WORD[ticketState(ticket.status, ticket.draft, ticket.kind)] ??
    (raw ? `“${raw}”` : null);
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
  if (cursor < title.length)
    runs.push({ text: title.slice(cursor), code: false });
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
export function mergeTicket(
  cached: TicketRef,
  live: TicketRef | undefined,
): TicketRef {
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
  return (
    session.title ??
    session.first_prompt ??
    session.git_branch ??
    session.session_id
  );
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

export function toolCommandLanguage(
  toolName: string,
  fieldName: string,
): "bash" | "javascript" | undefined {
  if (!COMMAND_FIELD_NAMES.has(fieldName)) return undefined;
  const name = toolName.toLowerCase();
  if (SHELL_TOOL_NAMES.has(name)) return "bash";
  if (CODE_MODE_TOOL_NAMES.has(name)) return "javascript";
  return undefined;
}
