import type { SessionTurnView, WorkspaceQueueView } from "@/lib/grove/api";

/**
 * The just-sent prompt, held locally until the reader can see it somewhere real.
 *
 * WHY THIS EXISTS AT ALL. Pressing send clears the composer instantly (that is
 * assistant-ui's own behaviour) and then nothing appears for up to a couple of
 * seconds: the daemon polls on a ~2 s tick, so the transcript learns about the
 * message on the next fingerprint invalidation. A prompt that vanishes from the
 * composer and shows up nowhere reads as a dropped message, which is the
 * complaint this answers.
 *
 * WHY IT IS NOT AN OPTIMISTIC TRANSCRIPT TURN. That was tried and removed, and
 * the reason is structural rather than a bug that could be fixed: the transcript
 * cache is addressed by ORDINAL. Appending a fake turn makes the client claim
 * one more turn than the session has, the next cursor asks to resume from an
 * index the daemon has never issued, the window comes back empty, and the merge
 * drops the fake turn again. The message flashed and vanished, and whether you
 * saw it at all depended on how busy the agent happened to be. Nothing about
 * windowing changes that arithmetic.
 *
 * So the echo lives BESIDE the transcript, never inside it — the same rule the
 * live question card follows, and for the same reason: only the daemon may say
 * what the transcript contains.
 */
export interface SendEcho {
  /** Exactly the text Grove sent — Grove forwards it verbatim, so an equality
   * check against what comes back is exact rather than a fuzzy match. */
  readonly text: string;
  /** When it was handed to the daemon, so a turn that predates it can never be
   * mistaken for its arrival. */
  readonly sentAt: string;
}

/**
 * Has the sent prompt become visible somewhere the reader is already looking?
 *
 * Two independent landing places, because a message goes to one or the other
 * depending on a race the client does not control: an IDLE agent takes it
 * straight in and it surfaces as a transcript turn, while a BUSY one has it
 * held by the harness and it surfaces in the steer queue. Checking both is what
 * makes the echo clear on the first of the two to happen, rather than lingering
 * as a duplicate beside the real thing.
 *
 * Pure, and separate from the hook that owns the state, so the rule that
 * decides when a user's own message stops being "in flight" is exercisable
 * without a query client — the same split every adapter in `lib/grove/adapters`
 * follows.
 */
export function echoLanded(
  echo: SendEcho,
  turns: readonly SessionTurnView[] | undefined,
  queue: WorkspaceQueueView | null | undefined,
): boolean {
  if (queue?.messages.some((message) => message.text === echo.text)) return true;
  return (turns ?? []).some((turn) => turn.user_text === echo.text && startedAfter(turn, echo));
}

/**
 * Did this turn begin at or after the send?
 *
 * The guard matters because a reader who sends the same prompt twice — "run the
 * tests" is not an unusual thing to type again — would otherwise have the SECOND
 * echo cleared instantly by the FIRST send's turn, which is already in the
 * transcript. A turn with no recorded start cannot be placed in time; treat it
 * as a landing rather than leaving the echo stuck forever, because a stale echo
 * pinned above the composer is the more visible failure of the two.
 */
function startedAfter(turn: SessionTurnView, echo: SendEcho): boolean {
  if (!turn.started_at) return true;
  return turn.started_at >= echo.sentAt;
}
