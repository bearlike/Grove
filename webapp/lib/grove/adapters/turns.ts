import type { SessionTurnView } from "@/lib/grove/api";

/**
 * Merging a windowed `/turns` response into the transcript already on screen.
 *
 * Pure, and separate from `transcript.ts` (which maps turns to assistant-ui
 * messages) because this answers a different question: not "how does a turn
 * render" but "may this window be placed against what we hold". That question
 * is where a transcript silently loses a turn, so it is the one worth pinning
 * without a daemon.
 */

/**
 * The fields of a turns response this merge reads.
 *
 * Deliberately NARROWER than `SessionDetailView` — the wire view is structurally
 * assignable to it, and naming only what the decision consumes keeps the merge
 * honest about its inputs rather than inheriting a shape it mostly ignores.
 */
export interface TurnWindow {
  turns: readonly SessionTurnView[];
  /** How many turns the session has in total, whatever this window carries. */
  total_turns: number;
  /** The index `turns[0]` sits at in the whole session. */
  first_turn_index: number;
  /** True only when the daemon HONOURED the cursor. False means "replace". */
  incremental: boolean;
}

/**
 * What `turnCursor` and `mergeTurns` need from what the client already holds
 * — just enough to place a new window against it, never the whole
 * `SessionDetailView` it happens to come from.
 */
export type HeldWindow = Pick<TurnWindow, "turns" | "first_turn_index">;

export type TurnMerge =
  /**
   * Use these turns as the whole transcript, starting at `first_turn_index`.
   * Carried explicitly rather than assumed to be `window.first_turn_index`,
   * because a follow-merge's result starts wherever the PREFIX it kept from
   * `held` starts, not wherever the fetched window itself started.
   */
  | { kind: "replace"; turns: readonly SessionTurnView[]; first_turn_index: number }
  /**
   * Nothing moved. Carries no turns ON PURPOSE: the caller must keep the array
   * it already has, and a payload here is how someone would accidentally hand
   * back a fresh-but-equal array — which re-runs the whole wire→assistant-ui
   * conversion for a transcript that did not change.
   */
  | { kind: "unchanged" }
  /** The window cannot be placed. Re-read the session with no cursor. */
  | { kind: "refetch" };

/**
 * The cursor to ask for, given what we already hold.
 *
 * ABSOLUTE — `held.first_turn_index + held.turns.length - 1` — never
 * `held.turns.length - 1` alone. The two coincide only while `held` starts at
 * session index 0, which was the ONLY shape a client could hold before
 * windowed reads existed; the moment the first read becomes a tail (see
 * `INITIAL_TURN_WINDOW`), `held.turns.length - 1` is a position *within the
 * window*, not the session, and asking for it as a cursor would silently
 * splice a hole between index 0 and wherever the window actually starts.
 *
 * Otherwise unchanged: `after_turn` is inclusive of its own index, turns are
 * append-*mostly*, and the last one keeps growing while the agent streams
 * parts and resolves tool calls. Asking past it would freeze a half-finished
 * turn on screen for the rest of the session. Holding nothing means no cursor
 * at all — there is no last-known turn to re-read.
 */
export function turnCursor(held: HeldWindow | undefined): number | undefined {
  return held && held.turns.length > 0 ? held.first_turn_index + held.turns.length - 1 : undefined;
}

/**
 * Place a window against the turns already held.
 *
 * Fail-safe by construction: anything this cannot prove contiguous asks for a
 * whole read rather than splicing a hole into the transcript. A cheap
 * transcript that has silently lost a turn is worse than an expensive one.
 *
 * The saving is on the WIRE, not in the render. Recombining is simply what a
 * windowed response requires; it does not make the client-side conversion
 * cheaper, because `messagesFromTurns` builds new message objects from every
 * turn whenever the array identity changes — and the array identity changes on
 * any tick that carries a tail. Keeping the prefix objects is still the right
 * default (it costs nothing and it is what a per-turn memo would need to become
 * useful), but do not mistake it for a live optimisation.
 *
 * TWO shapes of window reach here, and they take different branches on
 * purpose:
 *
 *   - An INCREMENTAL window is the answer to an `after_turn` cursor: the
 *     daemon has told us its start is exactly the index we asked to resume
 *     from, so it is provably contiguous with `held` and the two splice
 *     together, keeping `held`'s own prefix objects by reference.
 *   - A NON-incremental window carries no such promise — it is a `last`-sized
 *     fetch (including a "load earlier" widen) or the daemon declining a
 *     cursor it could not honour — so it is taken wholesale, as the complete
 *     answer in its own right. This is what makes a widen safe without any
 *     special-casing: `last` never claims contiguity with anything, and a
 *     widen's response already contains both the new earlier prefix AND the
 *     tail we already had, fetched together, so replacing wholesale is
 *     already "one contiguous range with no duplicated turns" — there is
 *     nothing left to splice.
 */
export function mergeTurns(held: HeldWindow | undefined, window: TurnWindow): TurnMerge {
  if (!window.incremental) {
    return { kind: "replace", turns: window.turns, first_turn_index: window.first_turn_index };
  }

  // Incremental against nothing to place it on. Only reachable if a cursor
  // went out for a transcript we no longer hold.
  if (!held) return { kind: "refetch" };

  const heldEnd = held.first_turn_index + held.turns.length;

  // A window starting past the end of what we hold would leave the turns
  // between unaccounted for. Contiguity is guaranteed by `turnCursor`, so this
  // means the request and the response disagree — never splice through it.
  if (window.first_turn_index > heldEnd) return { kind: "refetch" };

  // The offset into `held.turns` to keep, relative to where `held` itself
  // starts — NOT to `window.first_turn_index` alone, which was only ever a
  // valid slice point while `held` started at session index 0. A negative
  // offset means the window starts before `held` does, which `turnCursor`
  // never asks for; refuse rather than guess at what would fill the gap.
  const prefixCount = window.first_turn_index - held.first_turn_index;
  if (prefixCount < 0) return { kind: "refetch" };

  const turns = [...held.turns.slice(0, prefixCount), ...window.turns];
  if (turns.length === held.turns.length && window.turns.length === 0) return { kind: "unchanged" };
  return { kind: "replace", turns, first_turn_index: held.first_turn_index };
}
