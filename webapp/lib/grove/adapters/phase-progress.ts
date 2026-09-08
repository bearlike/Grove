/**
 * What the phase track renders, derived from claims the wire already carries.
 *
 * Pure by the layer's rule: every input is a plain wire shape and the output is
 * data, so the whole of this file is testable without a daemon, a clock or a
 * DOM. The one impure input — "what time is it now" — is a PARAMETER rather
 * than a `Date.now()` call, because a server and a browser never agree on it
 * (`relative-time.tsx` pays the same tax by mounting first).
 */

import type { PhaseView, ProgressEntryView, TodoProgressView } from "../api";

/** Where one step falls relative to the reported position. */
export type StepState = "complete" | "current" | "ahead";

/**
 * How far through the CURRENT phase the agent claims to be, and what said so.
 *
 * `fraction` is null when nothing licenses a claim — no checklist was reported,
 * or it is empty. A null is not a zero: an empty in-phase segment would say
 * "this phase has made no progress", which is a measurement nobody took. The
 * §11 fabricated-zero rule, applied to a sub-step.
 */
export type InPhaseProgress = {
  readonly fraction: number | null;
  readonly completed: number;
  readonly total: number;
};

/**
 * Where one step falls relative to the reported position.
 *
 * Grove keeps only the agent's LATEST claim — the phase file is overwritten in
 * place — so "before the reported index" is read as PASSED THROUGH rather than
 * as "individually visited". A BACKWARD report renders exactly like any other
 * position: this is a pure function of the current index, so a step that used
 * to read complete simply reads ahead again. Nothing marks the transition,
 * because the report is correct rather than a fault to flag.
 */
export function stepState(index: number, reached: number): StepState {
  if (index < reached) return "complete";
  if (index === reached) return "current";
  return "ahead";
}

/**
 * `done` completes the list, so nothing is "current" — every step reads
 * complete. Past-the-end comes from `phase.total`, THE WIRE'S OWN COUNT, so a
 * client never pins a second copy of the vocabulary.
 */
export function activeIndex(phase: PhaseView): number {
  return phase.phase === "done" ? phase.total : phase.index;
}

/**
 * The checklist, read as progress through the phase the agent is standing in.
 *
 * This is the ONE honest source for a sub-step magnitude: the agent's own
 * checklist is the only thing it reports that changes while a phase is held.
 * Elapsed time is not a substitute — a phase has no expected duration, so a
 * clock-driven bar would invent a denominator and then imply lateness from it.
 */
export function inPhaseProgress(
  todo: TodoProgressView | null | undefined,
): InPhaseProgress | null {
  if (!todo || todo.total <= 0) return null;
  return {
    fraction: todo.completed / todo.total,
    completed: todo.completed,
    total: todo.total,
  };
}

/**
 * When the agent entered the phase it is reporting now — from the durable
 * progress timeline, never from a new wire field.
 *
 * The rows arrive newest-first and each one is a claim AS MADE, so the entry
 * that began this phase is the OLDEST row in the unbroken run of same-phase
 * rows at the head. A re-report of the same phase (a note update) must not
 * restart the clock, and a backward report to a phase held earlier must not
 * reach past the intervening rows to that older visit — walking the head run
 * and stopping at the first row that disagrees gives both.
 *
 * Workspace-level rows only: a `ticket_key` row is a claim about a ticket, and
 * folding it in would let one ticket's phase reset the workspace's clock.
 */
export function phaseEnteredAt(
  phase: PhaseView,
  entries: readonly ProgressEntryView[] | null | undefined,
): string | null {
  if (!entries || entries.length === 0) return null;
  let entered: string | null = null;
  for (const entry of entries) {
    if (entry.ticket_key) continue;
    if (entry.phase !== phase.phase) break;
    entered = entry.recorded_at;
  }
  return entered;
}

/**
 * A report is stale when the agent is still working and has said nothing for
 * long enough that a reader would reasonably wonder.
 *
 * Both conditions are load-bearing. A finished workspace's last report is hours
 * old and perfectly honest, so `working` gates it; and an agent that reported
 * two minutes ago is not quiet however long the phase has run, so the age is
 * measured on the REPORT rather than on the phase.
 */
export const STALE_REPORT_MS = 30 * 60 * 1000;

export function reportIsStale(
  phase: PhaseView,
  working: boolean,
  now: number,
): boolean {
  if (!working || phase.phase === "done") return false;
  const reported = Date.parse(phase.updated_at);
  if (Number.isNaN(reported)) return false;
  return now - reported >= STALE_REPORT_MS;
}

/**
 * Whether the current step should pulse.
 *
 * Motion means "something is happening here", so it is spent only where that is
 * true: an agent actually working, on a phase it can still leave, and not one
 * it has reported itself stuck in. A blocked workspace is the case a pulse
 * would most misrepresent — nothing is moving, and an animated mark beside the
 * word `blocked` reads as activity.
 */
export function stepIsLive(phase: PhaseView, working: boolean): boolean {
  return working && !phase.blocked && phase.phase !== "done";
}
