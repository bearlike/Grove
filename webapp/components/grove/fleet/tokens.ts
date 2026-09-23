import type { VariantProps } from "class-variance-authority";
import {
  AlarmClockOffIcon,
  BellRingIcon,
  BotIcon,
  BoxIcon,
  CircleCheckBigIcon,
  CircleCheckIcon,
  CircleDashedIcon,
  CircleDotIcon,
  CirclePauseIcon,
  CirclePlayIcon,
  CircleSlashIcon,
  CircleStopIcon,
  CircleXIcon,
  Clock3Icon,
  HammerIcon,
  HourglassIcon,
  LoaderCircleIcon,
  MailXIcon,
  MessageCircleQuestionIcon,
  OctagonAlertIcon,
  RadioTowerIcon,
  SendIcon,
  ServerIcon,
  UnplugIcon,
  type LucideIcon,
} from "lucide-react";

import type { badgeVariants } from "@/components/ui/badge";
import type { GlossaryTerm } from "@/components/grove/glossary";
import type { AgentState, Runtime, TaskPhase, WatchState, WorkspaceStatus } from "./types";

type BadgeVariant = NonNullable<VariantProps<typeof badgeVariants>["variant"]>;

/**
 * Every status word and every emphasis level the fleet renders, in one table
 * per axis.
 *
 * Emphasis is a `Badge` variant, never a colour class: which hue means
 * "something is wrong" is the vendored layer's decision, and these tables only
 * say how loud each state should be. `destructive` is spent on the states that
 * want a human; everything else stays quiet so a fleet of twenty reads as calm.
 *
 * Ordinary live states stay neutral. Attention earns the filled signal;
 * progress is shape and text, never a field of yellow pills.
 */
const STATUS_TONE: Record<WorkspaceStatus, BadgeVariant> = {
  active: "secondary",
  running: "secondary",
  provisioning: "outline",
  idle: "secondary",
  paused: "secondary",
  offline: "outline",
  orphaned: "outline",
  error: "destructive",
};

/**
 * THIS TABLE ALSO CARRIES "NEEDS YOU", which is why three states are
 * `destructive` rather than one.
 *
 * The daemon derives `needs_attention` as `state ∈ {waiting, blocked, error}`
 * (`ATTENTION_STATES` in `core/agents/model.py`) — so it is not a fourth fact, it
 * is a function of this very column. The card used to render both, which put
 * "waiting for you" and "needs you" adjacent, saying one thing twice. Dropping
 * the separate mark would have made the fleet's most important signal *quieter*,
 * so the loudness moved here instead: the precise word survives, at the tone the
 * generic badge was carrying.
 *
 * Keep this in step with `ATTENTION_STATES` — they are two spellings of one rule,
 * and the wire union is what makes a drift fail to compile rather than fade.
 */
const AGENT_TONE: Record<AgentState, BadgeVariant> = {
  starting: "outline",
  working: "outline",
  waiting: "destructive",
  blocked: "destructive",
  idle: "secondary",
  error: "destructive",
  unknown: "secondary",
};

/**
 * The human wording and glyph of every fleet state.
 *
 * **All UI labels are sentence case**: only the first word and proper names
 * capitalize. State words are labels rather than headings, so title case turns
 * a compact set of facts into visual noise. Keeping the word and its mark in
 * one total table means the rail, filter, card and palette cannot slowly teach
 * different names for the same state.
 *
 * The marks deliberately preserve the TUI's meaning without copying its
 * terminal glyphs: live/run, quiet/wait, pause, unavailable, stranded, error;
 * and spin-up, work, human input, quiet, unknown. A Lucide shape is the web
 * expression of that vocabulary, not decoration picked at a call site.
 */
const STATUS_PRESENTATION: Record<WorkspaceStatus, { label: string; Icon: LucideIcon }> = {
  active: { label: "Active", Icon: RadioTowerIcon },
  running: { label: "Active", Icon: RadioTowerIcon },
  provisioning: { label: "Provisioning", Icon: LoaderCircleIcon },
  idle: { label: "Idle", Icon: Clock3Icon },
  paused: { label: "Paused", Icon: CirclePauseIcon },
  offline: { label: "Offline", Icon: UnplugIcon },
  orphaned: { label: "Orphaned", Icon: HammerIcon },
  error: { label: "Error", Icon: CircleXIcon },
};

const AGENT_PRESENTATION: Record<
  AgentState,
  { label: string; Icon: LucideIcon; attentionLabel?: string }
> = {
  starting: { label: "Starting", Icon: LoaderCircleIcon },
  working: { label: "Working", Icon: CirclePlayIcon },
  waiting: {
    label: "Waiting for you",
    Icon: MessageCircleQuestionIcon,
    attentionLabel: "Waiting for you — the agent asked a question",
  },
  blocked: {
    label: "Blocked",
    Icon: OctagonAlertIcon,
    attentionLabel: "Blocked — waiting on a permission or an external dependency",
  },
  idle: { label: "Idle", Icon: CircleDashedIcon },
  error: {
    label: "Error",
    Icon: CircleXIcon,
    attentionLabel: "Error — the session hit a failure",
  },
  unknown: { label: "No session", Icon: BotIcon },
};

const RUNTIME_PRESENTATION: Record<Runtime, { label: string; Icon: LucideIcon }> = {
  host: { label: "Host", Icon: ServerIcon },
  container: { label: "Container", Icon: BoxIcon },
};

/**
 * Which of these status words a newcomer cannot decode from the word alone.
 *
 * Most of `WorkspaceStatus` is ordinary English (`active`, `paused`, `error`)
 * and stays unexplained per the glossary's own admission rule — a definition
 * on every badge is a page arguing with itself. These three are Grove-specific
 * enough that the plain word invites the wrong guess: "offline" reads as a
 * network state rather than "the agent process died, respawn brings it back",
 * and "orphaned" and "provisioning" have no everyday meaning to fall back on.
 * `Partial` on purpose — an entry's absence IS the "leave it alone" decision.
 */
const STATUS_GLOSSARY: Partial<Record<WorkspaceStatus, GlossaryTerm>> = {
  provisioning: "status_provisioning",
  offline: "status_offline",
  orphaned: "status_orphaned",
};

/**
 * Same rule, the agent axis. `waiting` already reads as "waiting for you" in
 * its own label; `blocked` does not say that it means an explicit permission
 * or input prompt, which is the one distinction worth a sentence.
 */
const AGENT_GLOSSARY: Partial<Record<AgentState, GlossaryTerm>> = {
  blocked: "agent_blocked",
};

/**
 * A task phase is a named position, not merely a fraction. Each position owns a
 * distinct Lucide silhouette so the ramp survives greyscale and every web
 * surface speaks the same vocabulary; the TUI retains its terminal-safe form.
 */
const PHASE_PRESENTATION: Record<TaskPhase, { label: string; Icon: LucideIcon }> = {
  scope: { label: "Scope", Icon: CircleDashedIcon },
  plan: { label: "Plan", Icon: CircleDotIcon },
  build: { label: "Build", Icon: CirclePlayIcon },
  verify: { label: "Verify", Icon: CircleCheckBigIcon },
  deliver: { label: "Deliver", Icon: SendIcon },
  handoff: { label: "Handoff", Icon: CircleCheckIcon },
};

/**
 * The runtime's definition, in ONE place — the glossary's own
 * `container_runtime`/`host_runtime` entries, not a second sentence saying the
 * same thing. This replaces an earlier `RUNTIME_HINT` table that restated the
 * identical fact in different words, which is exactly the drift the glossary
 * module exists to prevent: two explanations of one term teaches it twice and
 * trusts neither.
 */
const RUNTIME_GLOSSARY: Record<Runtime, GlossaryTerm> = {
  host: "host_runtime",
  container: "container_runtime",
};

/**
 * The vendor whose mark a row leads with. `generic` is not a failure: Grove
 * launches shells and in-house agents that have no brand at all, and inventing
 * one for them would be a worse lie than a neutral glyph.
 *
 * `codex` is its own brand rather than folding into `openai`: the two vendors
 * ship distinct marks (lobehub's `Codex` icon, not `OpenAI`), and a shared
 * brand would force one glyph to stand in for two identities.
 */
export type AgentBrand = "claude" | "codex" | "opencode" | "openai" | "gemini" | "generic";

/**
 * Matched on the agent's configured NAME, because that is the only vendor
 * signal the fleet wire carries — the adapter kind lives on a session, and a
 * workspace whose agent has never started has no session to read it from.
 * First match wins, so order these by specificity — `codex` before `openai`
 * matters because "codex" itself has no other vendor word in it.
 *
 * `opencode` must precede `openai` for the SAME reason and it is the sharper
 * trap: "OpenCode" CONTAINS "open" but the `openai` pattern's `/openai|gpt/i`
 * does not match it, while a laxer pattern would — and the failure is silent,
 * since one vendor's mark on another vendor's row still renders. OpenCode is
 * its own brand rather than folding into `openai` because they are unrelated
 * vendors that happen to share three letters.
 */
const AGENT_BRAND_PATTERNS: readonly (readonly [RegExp, AgentBrand])[] = [
  [/claude|anthropic/i, "claude"],
  [/codex/i, "codex"],
  [/opencode/i, "opencode"],
  [/openai|gpt/i, "openai"],
  [/gemini/i, "gemini"],
];

export function agentBrand(agentName: string): AgentBrand {
  return AGENT_BRAND_PATTERNS.find(([pattern]) => pattern.test(agentName))?.[1] ?? "generic";
}

/** Progress stays neutral; only completion earns a positive text accent. */
const ACCENT = {
  progress: "text-content-secondary",
  done: "text-success",
} as const;

/** Resting states have no accent; the phase glyph still carries position. */
const STATUS_ACCENT: Partial<Record<WorkspaceStatus, string>> = {
  provisioning: ACCENT.progress,
};

const AGENT_ACCENT: Partial<Record<AgentState, string>> = {
  starting: ACCENT.progress,
  working: ACCENT.progress,
};

/**
 * Preserve wire state before AgentStatus folds idle into its `done` glyph.
 * Idle is not reported completion, so it stays tertiary rather than success.
 * The header consumes these semantic fills instead of the vendor's palette.
 */
const AGENT_PILL_ACCENT: Record<AgentState, string> = {
  starting: "bg-primary",
  working: "bg-primary",
  waiting: "bg-destructive",
  blocked: "bg-destructive",
  error: "bg-destructive",
  idle: "bg-content-tertiary",
  unknown: "bg-content-tertiary",
};

/**
 * A subagent's run, told as a run: running, finished, or stopped.
 *
 * NOT the agent axis above, because a subagent is not something a person drives.
 * That axis spends `destructive` on "waiting for you", which is true of a root
 * session asking its human and false of a child, whose closed turn means it
 * handed its result back to the parent. None of these three wants a human, so
 * none is loud: shape and word carry the difference (§4.7), and only work in
 * flight takes the progress accent.
 */
export type SubagentRun = "running" | "finished" | "stopped";

const SUBAGENT_RUN_PRESENTATION: Record<
  SubagentRun,
  { label: string; Icon: LucideIcon; tone: BadgeVariant; accent?: string }
> = {
  running: { label: "Running", Icon: CirclePlayIcon, tone: "outline", accent: ACCENT.progress },
  finished: { label: "Finished", Icon: CircleCheckIcon, tone: "secondary" },
  stopped: { label: "Stopped", Icon: CircleStopIcon, tone: "secondary" },
};

export function subagentRunLabel(run: SubagentRun): string {
  return SUBAGENT_RUN_PRESENTATION[run].label;
}

export function subagentRunGlyph(run: SubagentRun): LucideIcon {
  return SUBAGENT_RUN_PRESENTATION[run].Icon;
}

export function subagentRunTone(run: SubagentRun): BadgeVariant {
  return SUBAGENT_RUN_PRESENTATION[run].tone;
}

export function subagentRunAccent(run: SubagentRun): string | undefined {
  return SUBAGENT_RUN_PRESENTATION[run].accent;
}

/**
 * The rail's state pills: a SOFT tint of the claim's own hue, never a solid fill.
 *
 * A rail lists a dozen rows, and a solid destructive badge on each waiting one
 * turns the column into a wall of red that no single row can rise above. The
 * tint keeps the hue as the carrier (shape and word still lead, §4.7) while the
 * fleet card, a surface you stop on, keeps the solid `AGENT_TONE` badge. The
 * four keys are the rail's only claims: attention, and phase in its three
 * readings — in progress, handed off, and blocked.
 */
const RAIL_PILL_ACCENT = {
  attention: "border-transparent bg-destructive/15 text-destructive",
  progress: "border-transparent bg-muted text-content-secondary",
  done: "border-transparent bg-success/15 text-success",
  blocked: "border-transparent bg-warning/15 text-warning",
} as const;

export type RailPillTone = keyof typeof RAIL_PILL_ACCENT;

export function railPillAccent(tone: RailPillTone): string {
  return RAIL_PILL_ACCENT[tone];
}

export function statusTone(status: WorkspaceStatus): BadgeVariant {
  return STATUS_TONE[status];
}

export function statusAccent(status: WorkspaceStatus): string | undefined {
  return STATUS_ACCENT[status];
}

export function agentTone(state: AgentState): BadgeVariant {
  return AGENT_TONE[state];
}

export function agentAccent(state: AgentState): string | undefined {
  return AGENT_ACCENT[state];
}

/** The header pill dot's fill, from the same table the sidebar marks read. */
export function agentPillAccent(state: AgentState): string {
  return AGENT_PILL_ACCENT[state];
}

/**
 * A bounded count's accent: amber while it is running, green once it is all in.
 *
 * The one place a *quantity* earns a hue, and it earns it because the quantity
 * is a completion — the same fact `done`/`in flight` names on every other axis,
 * counted instead of stated. An empty or unstarted count gets nothing: zero of
 * six is not progress, and colouring it would say work had begun when none has.
 */
export function progressAccent(done: number, total: number): string | undefined {
  if (total <= 0 || done <= 0) return undefined;
  return done >= total ? ACCENT.done : ACCENT.progress;
}

export function statusLabel(status: WorkspaceStatus): string {
  return STATUS_PRESENTATION[status].label;
}

export function statusGlyph(status: WorkspaceStatus): LucideIcon {
  return STATUS_PRESENTATION[status].Icon;
}

export function agentLabel(state: AgentState): string {
  return AGENT_PRESENTATION[state].label;
}

export function agentGlyph(state: AgentState): LucideIcon {
  return AGENT_PRESENTATION[state].Icon;
}

/** The rail's attention label is only defined for states that need a human. */
export function attentionLabel(state: AgentState): string | undefined {
  return AGENT_PRESENTATION[state].attentionLabel;
}

export function statusGlossaryTerm(status: WorkspaceStatus): GlossaryTerm | undefined {
  return STATUS_GLOSSARY[status];
}

export function agentGlossaryTerm(state: AgentState): GlossaryTerm | undefined {
  return AGENT_GLOSSARY[state];
}

/** A phase block is a flag on a position, never a seventh step. */
export const PHASE_BLOCKED_ICON = OctagonAlertIcon;

export function phaseLabel(phase: TaskPhase): string {
  return PHASE_PRESENTATION[phase].label;
}

export function phaseGlyph(phase: TaskPhase): LucideIcon {
  return PHASE_PRESENTATION[phase].Icon;
}

export function runtimeLabel(runtime: Runtime): string {
  return RUNTIME_PRESENTATION[runtime].label;
}

/**
 * The runtime's glyph, in ONE place. `ServerIcon` / `BoxIcon` retain the TUI's
 * machine-versus-boundary distinction without treating a container as a parcel.
 */
export function runtimeGlyph(runtime: Runtime): LucideIcon {
  return RUNTIME_PRESENTATION[runtime].Icon;
}

export function runtimeGlossaryTerm(runtime: Runtime): GlossaryTerm {
  return RUNTIME_GLOSSARY[runtime];
}

/**
 * A watch's lifecycle — word, glyph and tone in one total table.
 *
 * Running, fired and expired each get their own word and shape, so hue never
 * carries the difference alone. `cancelled` and `undeliverable` are shown as
 * themselves rather than folded into one of those three: a withdrawal was
 * nobody's failure, and `undeliverable` is the one state that wants a human —
 * the watch settled and the agent was never told — so it alone is loud.
 */
const WATCH_PRESENTATION: Record<WatchState, { label: string; Icon: LucideIcon; tone: BadgeVariant }> = {
  pending: { label: "Running", Icon: HourglassIcon, tone: "outline" },
  fired: { label: "Fired", Icon: BellRingIcon, tone: "secondary" },
  expired: { label: "Expired", Icon: AlarmClockOffIcon, tone: "secondary" },
  cancelled: { label: "Cancelled", Icon: CircleSlashIcon, tone: "secondary" },
  undeliverable: { label: "Undeliverable", Icon: MailXIcon, tone: "destructive" },
};

/** Every watch state in the table's order — the order a summary counts them in. */
export const WATCH_STATES = Object.keys(WATCH_PRESENTATION) as WatchState[];

export function watchLabel(state: WatchState): string {
  return WATCH_PRESENTATION[state].label;
}

export function watchGlyph(state: WatchState): LucideIcon {
  return WATCH_PRESENTATION[state].Icon;
}

export function watchTone(state: WatchState): BadgeVariant {
  return WATCH_PRESENTATION[state].tone;
}
