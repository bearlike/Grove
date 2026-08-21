import type { VariantProps } from "class-variance-authority";
import {
  BotIcon,
  BoxIcon,
  CircleAlertIcon,
  CircleDashedIcon,
  CirclePauseIcon,
  CirclePlayIcon,
  CircleXIcon,
  Clock3Icon,
  HammerIcon,
  LoaderCircleIcon,
  MessageCircleQuestionIcon,
  RadioTowerIcon,
  ServerIcon,
  UnplugIcon,
  type LucideIcon,
} from "lucide-react";

import type { badgeVariants } from "@/components/ui/badge";
import type { GlossaryTerm } from "@/components/grove/glossary";
import type { AgentState, Runtime, TaskPhase, WorkspaceStatus } from "./types";

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
 * ONE `default` PER OBJECT, ACROSS ALL ITS AXES — and the workspace status axis
 * is the one that gets it. `active`/`running` and `working` both used to render
 * `default`, so a card put two maximally loud marks side by side to encode one
 * fact: an active workspace is active *because* its agent is working. The agent
 * axis therefore never claims the loudest tone; it is the axis with the finer
 * word, not the louder mark.
 */
const STATUS_TONE: Record<WorkspaceStatus, BadgeVariant> = {
  active: "default",
  running: "default",
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

const AGENT_PRESENTATION: Record<AgentState, { label: string; Icon: LucideIcon }> = {
  starting: { label: "Starting", Icon: LoaderCircleIcon },
  working: { label: "Working", Icon: CirclePlayIcon },
  waiting: { label: "Waiting for you", Icon: MessageCircleQuestionIcon },
  blocked: { label: "Blocked", Icon: CircleAlertIcon },
  idle: { label: "Idle", Icon: CircleDashedIcon },
  error: { label: "Error", Icon: CircleXIcon },
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
 * A task phase is a named position, not merely a fraction. The web preserves
 * its established grayscale-safe ramp while stating the phase in sentence case
 * beside it; the TUI uses its own terminal-safe rendering of the same ordered
 * phases.
 */
const PHASE_PRESENTATION: Record<TaskPhase, { label: string; glyph: string }> = {
  scoping: { label: "Scoping", glyph: "○" },
  planning: { label: "Planning", glyph: "◔" },
  implementing: { label: "Implementing", glyph: "◑" },
  verifying: { label: "Verifying", glyph: "◕" },
  delivering: { label: "Delivering", glyph: "●" },
  done: { label: "Done", glyph: "✓" },
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
export type AgentBrand = "claude" | "codex" | "openai" | "gemini" | "generic";

/**
 * Matched on the agent's configured NAME, because that is the only vendor
 * signal the fleet wire carries — the adapter kind lives on a session, and a
 * workspace whose agent has never started has no session to read it from.
 * First match wins, so order these by specificity — `codex` before `openai`
 * matters because "codex" itself has no other vendor word in it.
 */
const AGENT_BRAND_PATTERNS: readonly (readonly [RegExp, AgentBrand])[] = [
  [/claude|anthropic/i, "claude"],
  [/codex/i, "codex"],
  [/openai|gpt/i, "openai"],
  [/gemini/i, "gemini"],
];

export function agentBrand(agentName: string): AgentBrand {
  return AGENT_BRAND_PATTERNS.find(([pattern]) => pattern.test(agentName))?.[1] ?? "generic";
}

/**
 * The two hues `Badge`'s own variants cannot say, as semantic-token classes.
 *
 * `ui/badge` is vendored, so its variant table is not ours to extend, and the
 * two states the fleet most needs to distinguish are exactly the two it omits:
 * work that is UNDER WAY and work that is DONE. `destructive` covers failure and
 * `default`/`secondary` cover loud/quiet, which leaves "in flight" and "finished"
 * sharing a grey.
 *
 * These are tokens, never palette classes, so `lint:styling` passes them and the
 * theme still owns what they resolve to — the same standing the vendored
 * variants have. They ride `className` because that is the only seam a vendored
 * component leaves; §6's "never restyled at a call site" still holds, because a
 * call site reads one of the tables below and never writes a class itself.
 */
const ACCENT = {
  progress: "bg-warning text-warning-foreground",
  done: "bg-success text-success-foreground",
} as const;

/**
 * Which states earn one, and the gate is the same on every axis: an accent is
 * spent only where the object is MID-FLIGHT or FINISHED, never on identity and
 * never on a resting state. `provisioning` and `starting`/`working` are the
 * in-flight cases; nothing on these two axes is ever "finished", because a
 * workspace that finished is simply idle again.
 */
const STATUS_ACCENT: Partial<Record<WorkspaceStatus, string>> = {
  provisioning: ACCENT.progress,
};

const AGENT_ACCENT: Partial<Record<AgentState, string>> = {
  starting: ACCENT.progress,
  working: ACCENT.progress,
};

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

export function statusGlossaryTerm(status: WorkspaceStatus): GlossaryTerm | undefined {
  return STATUS_GLOSSARY[status];
}

export function agentGlossaryTerm(state: AgentState): GlossaryTerm | undefined {
  return AGENT_GLOSSARY[state];
}

/**
 * BLOCKED IS A FLAG ACROSS THE RAMP, NOT A SEVENTH STEP, so it takes the glyph
 * slot and leaves the position to be reported by the number beside it.
 *
 * The two facts are independent — how far the agent got, and whether it is
 * still moving — and the ramp above can only express the first. Replacing the
 * fill stage therefore loses nothing on `PhaseBadge`, whose `n/total` fraction
 * carries the position anyway; it buys a mark a reader can find on a wall of
 * twenty cards without reading a single word.
 *
 * `⊘` stays inside the ramp's own circle family deliberately, so it reads as
 * "this ramp, halted" rather than as a glyph borrowed from some other
 * vocabulary — and, like every character above, it survives greyscale, which
 * is the whole reason this axis is drawn in shapes (§4.7).
 */
const BLOCKED_GLYPH = "⊘";

export function phaseLabel(phase: TaskPhase): string {
  return PHASE_PRESENTATION[phase].label;
}

export function phaseGlyph(phase: TaskPhase, blocked = false): string {
  return blocked ? BLOCKED_GLYPH : PHASE_PRESENTATION[phase].glyph;
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
