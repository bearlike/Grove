import type { ComponentProps } from "react";

import type { AgentState, AgentStatus } from "@/components/elements/agent-status";
import type { ConnectionState } from "@/components/elements/connection-state";
import type { AgentActivityView, PhaseView } from "@/lib/grove/api";

/**
 * The agent's live axes → props for the vendored `AgentStatus` /
 * `ConnectionState`.
 *
 * Grove reports three independent things about a workspace — the tmux/container
 * lifecycle, the agent's activity, and the task phase the agent writes for
 * itself. Only the last two say anything about what the agent is DOING, so the
 * status pill blends exactly those: activity picks the mark, phase picks the
 * words.
 *
 * Time is a parameter, never `Date.now()` — the caller owns the clock.
 */

/** The wire's agent activity states. Seven values drive branching here, so the
 * union is named rather than inlined. */
export type AgentActivityState = AgentActivityView["state"];

/** The wire's six task phases. */
export type TaskPhase = PhaseView["phase"];

/** The data half of `AgentStatus`'s props. */
export type AgentStatusProps = Pick<
  ComponentProps<typeof AgentStatus>,
  "state" | "label" | "elapsed"
>;

/** The data half of `ConnectionState`'s props — the consumer owns `onRetry`. */
export type ConnectionStateProps = Pick<
  ComponentProps<typeof ConnectionState>,
  "phase" | "attempt"
>;

/**
 * Grove's seven activity states onto the vendored element's three.
 *
 * `waiting` is the element's "the run is up but not advancing" mark, which is
 * the honest read for anything blocked, errored or unknowable; `done` is the
 * quiet resting mark, so only a genuinely idle agent takes it.
 */
const AGENT_STATE: Record<AgentActivityState, AgentState> = {
  starting: "working",
  working: "working",
  waiting: "waiting",
  blocked: "waiting",
  error: "waiting",
  unknown: "waiting",
  idle: "done",
};

/** The fallback word per activity state, used when neither the phase nor the
 * agent's own task line says anything. */
const AGENT_LABEL: Record<AgentActivityState, string> = {
  starting: "Starting",
  working: "Working",
  waiting: "Waiting for you",
  blocked: "Blocked",
  error: "Errored",
  unknown: "Unknown",
  idle: "Idle",
};

/** The phase an agent reports for itself, as a headline. */
const PHASE_LABEL: Record<TaskPhase, string> = {
  scoping: "Scoping",
  planning: "Planning",
  implementing: "Implementing",
  verifying: "Verifying",
  delivering: "Delivering",
  done: "Done",
};

/**
 * The status pill for one session.
 *
 * The label prefers the phase note (the agent's own sentence about what it is
 * doing), then the phase word, then the transcript-derived task line, then the
 * state word — most specific claim first, and every tier is something the agent
 * actually said.
 *
 * `elapsed` is only offered while a generation is genuinely in flight: the wire
 * stamps `live.generating_since` at the start of one, so its absence means
 * there is no interval to count and the field is withheld rather than filled
 * from a message timestamp that would keep ticking after the agent stopped.
 *
 * A BLOCKED PHASE PREFIXES THE LABEL RATHER THAN REPLACING IT. `blocked` is a
 * flag across the phase axis, not a seventh phase, so the reader still learns
 * WHICH phase is stuck — and the word leads, because the vendored pill's mark
 * belongs to the agent-activity axis and cannot be made to carry this one.
 * `state` is deliberately untouched for the same reason: an agent may be
 * genuinely working while the task it reports is blocked on somebody else, and
 * overwriting the activity mark would make the pill claim the process stalled.
 */
export function agentStatusProps(
  activity: AgentActivityView,
  phase: PhaseView | null,
  now: Date,
): AgentStatusProps {
  const generatingSince = activity.live?.generating_since;
  const elapsed = generatingSince ? formatElapsed(generatingSince, now) : undefined;
  const said =
    phase?.note?.trim() ||
    (phase ? PHASE_LABEL[phase.phase] : undefined) ||
    activity.current_task?.trim() ||
    AGENT_LABEL[activity.state] ||
    "Unknown";
  return {
    state: AGENT_STATE[activity.state] ?? "waiting",
    label: phase?.blocked ? `Blocked — ${said}` : said,
    ...(elapsed === undefined ? {} : { elapsed }),
  };
}

/**
 * The event stream's health as a `ConnectionState` phase.
 *
 * `resumed` is deliberately never returned: it is a transient celebration of a
 * recovery, and a pure function over the current state cannot know a drop
 * preceded it. A consumer that wants it holds that edge itself.
 */
export function connectionStateProps(stream: {
  connected: boolean;
  attempt: number;
}): ConnectionStateProps {
  if (stream.connected) return { phase: "online" };
  return stream.attempt > 0
    ? { phase: "reconnecting", attempt: stream.attempt }
    : { phase: "dropped" };
}

/**
 * A wire instant to `now`, as `m:ss` or `h:mm:ss`.
 *
 * Clamped at zero: a container's clock can read ahead of the browser's, and a
 * negative duration reads as a bug where a stalled `0:00` reads as "just
 * started".
 */
export function formatElapsed(since: string, now: Date): string {
  const started = new Date(since).getTime();
  if (Number.isNaN(started)) return "";
  const seconds = Math.max(0, Math.floor((now.getTime() - started) / 1000));
  const parts = [Math.floor(seconds / 60) % 60, seconds % 60];
  const hours = Math.floor(seconds / 3600);
  if (hours > 0) parts.unshift(hours);
  return parts
    .map((part, index) => (index === 0 ? String(part) : String(part).padStart(2, "0")))
    .join(":");
}
