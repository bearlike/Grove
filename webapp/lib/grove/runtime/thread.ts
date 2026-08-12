"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  useExternalStoreRuntime,
  type AppendMessage,
  type AssistantRuntime,
  type ExternalStoreThreadListAdapter,
  type ThreadMessageLike,
} from "@assistant-ui/react";

import {
  messagesFromTurns,
  pendingQuestions,
  questionGroups,
  questionPresentation,
  type QuestionPresentation,
} from "@/lib/grove/adapters";
import type {
  AgentQuestionView,
  DashboardSnapshotView,
  QuestionAnswerItem,
  SessionSummaryView,
  SessionTurnView,
  TodoListView,
} from "@/lib/grove/api";
import {
  useAnswerQuestion,
  useInterrupt,
  useSendMessage,
  useSessionTurns,
  useTranscriptInvalidation,
  useWorkspaceQueue,
  useWorkspaceTodo,
} from "@/lib/grove/hooks";
import { refusalNotice } from "./notice";
import { echoLanded, type SendEcho } from "./sending";
import { sessionThreadList } from "./thread-list";

/**
 * Grove's transcript as an assistant-ui external-store runtime.
 *
 * The store's messages ARE the mapped wire turns, so the transcript is whatever
 * `/turns` says and nothing is held twice. Sending routes through `onNew`, which
 * fires the optimistic mutation — the store reflects `messages`, it never
 * appends a sent message itself.
 */

/** A no-op store setter: message branching is not a Grove concept (the daemon
 * owns the transcript), but the adapter still wants the seam. */
const NO_SET_MESSAGES = (): void => {};

/** A composer that can never fire — a read-only transcript renders none. */
const NO_SEND = async (): Promise<void> => {};

/**
 * Identity conversion — and it MUST be a module constant, not an inline arrow.
 *
 * `ExternalStoreThreadRuntimeCore.onStoreUpdated` reads:
 *
 *     if (oldStore.convertMessage !== store.convertMessage) this._converter = new ThreadMessageConverter();
 *     else if (oldStore.isRunning === store.isRunning && oldStore.messages === store.messages) { …skip… }
 *
 * The two branches are chained, so a `convertMessage` whose identity changes
 * every render doesn't merely discard the converter cache — it makes the
 * `messages === messages` fast path **unreachable**. Every render then
 * re-converts the whole transcript cold and re-inserts every message into the
 * repository: O(transcript) of object construction per render, growing with
 * the session. `messages` is already memoised on `[turns]`; before this the
 * memo was simply never consulted.
 */
const IDENTITY_CONVERT = <T,>(message: T): T => message;

export interface TranscriptRuntimeOptions {
  turns: readonly SessionTurnView[] | undefined;
  /** Omit for a read-only transcript. */
  onNew?: (message: AppendMessage) => Promise<void>;
  onCancel?: () => Promise<void>;
  /**
   * Read-only mode. Expressed as the runtime's own `isDisabled` capability
   * rather than by hiding a control: a historical session usually has no
   * workspace behind it, so the composer must be structurally unable to send,
   * not merely invisible.
   */
  readOnly?: boolean;
  threadList?: ExternalStoreThreadListAdapter;
}

/**
 * The transcript half of the runtime, on its own so BOTH surfaces that render
 * Grove turns share one mapping: the steerable workspace panel, and the
 * read-only catalog transcript, which has no workspace to steer.
 *
 * `isRunning` is deliberately LEFT UNSET. assistant-ui disables the composer
 * while a thread is running and injects a synthetic streaming placeholder — both
 * fight Grove's core model, where you steer a WORKING agent with follow-ups. The
 * working state is reported by the agent-status surfaces instead, and every
 * mapped assistant message pins `complete` so the heuristic stays false.
 */
export function useTranscriptRuntime({
  turns,
  onNew = NO_SEND,
  onCancel,
  readOnly = false,
  threadList,
}: TranscriptRuntimeOptions): { runtime: AssistantRuntime; messageCount: number } {
  const messages = useMemo(() => messagesFromTurns(turns ?? []), [turns]);

  const runtime = useExternalStoreRuntime<ThreadMessageLike>({
    messages,
    // The store's items are already `ThreadMessageLike`, so the converter is
    // identity — the wire→assistant-ui mapping happened once, purely, above.
    // Referentially stable on purpose; see IDENTITY_CONVERT.
    convertMessage: IDENTITY_CONVERT,
    onNew,
    ...(onCancel ? { onCancel } : {}),
    isDisabled: readOnly,
    setMessages: NO_SET_MESSAGES,
    ...(threadList ? { adapters: { threadList } } : {}),
  });

  return { runtime, messageCount: messages.length };
}

/** One live question batch, with the submit state it is currently in. */
export interface PendingQuestionGroup {
  /** The batch's `group_id` — also its answer-back address. */
  groupId: string;
  /** Every question in the batch; they answer atomically in one submit. */
  questions: AgentQuestionView[];
  /** The vendored element and props this batch renders as. */
  presentation: QuestionPresentation;
}

export interface GroveThreadState {
  runtime: AssistantRuntime;
  /** True when there is no transcript AND no live question — the empty state. */
  isEmpty: boolean;
  /**
   * The LIVE pending question batches.
   *
   * Rendered as a SIBLING of the message stream, never as a message: the
   * message list keys by position, so an interactive card at the tail would
   * remount — dropping in-flight selections — every time the transcript grows
   * under it.
   */
  pending: PendingQuestionGroup[];
  /** The agent's current plan, pinned above the composer. Null when it has none
   * or has cleared it. */
  todo: TodoListView | null;
  /** A steering refusal, for a quiet inline notice. */
  notice: string | null;
  /** The agent is working — interrupting an idle one is a 409. */
  canInterrupt: boolean;
  interrupt: () => void;
  /** Submit one batch's answers. */
  answer: (groupId: string, answers: QuestionAnswerItem[]) => void;
  /** True while an answer is in flight or has just landed. */
  answering: boolean;
  /** The prompt just sent, until it appears in the transcript or the steer
   * queue for real. Null the rest of the time. See `./sending`. */
  sending: string | null;
  /** True when the held window starts above turn 0 — there is earlier history
   * a reader has not seen yet. */
  hasEarlier: boolean;
  /** Widen the held window backwards by one doubling. See `useSessionTurns`. */
  loadEarlier: () => void;
  /** True while a `loadEarlier` fetch is in flight. */
  loadingEarlier: boolean;
}

export interface GroveThreadOptions {
  workspaceId: string;
  sessionId: string | null;
  /** The live activity snapshot the page already holds — the ONLY place a
   * PENDING question can come from, since the agent flushes nothing to the
   * transcript while one is on screen. */
  snapshot: DashboardSnapshotView | null;
  /** The workspace's sessions, for assistant-ui's thread list. */
  sessions?: readonly SessionSummaryView[];
  onSwitchSession?: (sessionId: string) => void;
}

/**
 * The full steerable thread: transcript, composer, interrupt, plan and live
 * questions, wired to one workspace's session.
 */
export function useGroveThread({
  workspaceId,
  sessionId,
  snapshot,
  sessions,
  onSwitchSession,
}: GroveThreadOptions): GroveThreadState {
  const turnsQuery = useSessionTurns(workspaceId, sessionId);
  const detail = turnsQuery.query.data;
  useTranscriptInvalidation(snapshot, workspaceId, sessionId);

  const send = useSendMessage(workspaceId);
  const interruptMutation = useInterrupt(workspaceId);
  const answerMutation = useAnswerQuestion(workspaceId);
  const [notice, setNotice] = useState<string | null>(null);

  const turns = detail?.turns;

  // NOT derived from `turns` any more. A todo write is a full REWRITE, so only
  // the newest board matters — but once the transcript is windowed to its
  // tail, the newest board is routinely OUTSIDE what is loaded (the agent may
  // have last written its plan hundreds of turns back). `useWorkspaceTodo`
  // reads the daemon's own `latest_todo` seam instead, which is correct
  // regardless of how much of the transcript the client currently holds.
  const todoQuery = useWorkspaceTodo(workspaceId);
  // An empty checklist renders as no plan at all — same rule the old
  // `latestTodoFromTurns` applied for a CLEARED plan, now applied uniformly
  // whether the agent cleared it or has simply never called a todo tool yet.
  const todo = useMemo(() => {
    const view = todoQuery.data;
    return view && view.items.length > 0 ? view : null;
  }, [todoQuery.data]);

  const rawPending = useMemo<PendingQuestionGroup[]>(() => {
    const live = pendingQuestions(snapshot, workspaceId, sessionId);
    return questionGroups(live).flatMap((group) => {
      const presentation = questionPresentation(group);
      return presentation
        ? [{ groupId: presentation.groupId, questions: group, presentation }]
        : [];
    });
  }, [snapshot, workspaceId, sessionId]);

  // `snapshot` is a brand-new object on every ~1Hz SSE frame (`hooks/stream.tsx`
  // calls `setQueryData` with the raw pushed payload), so the memo above recomputes
  // — and returns a fresh array — on every tick a question is on screen, which
  // defeats `ThreadPane`'s "hold the thread still" memo in `transcript.tsx` for as
  // long as one is pending. The actual rendered content only changes when a group
  // appears, resolves, or its presentation payload changes, so key on exactly that
  // and hand back the SAME array reference across ticks that didn't.
  const pending = useStableByKey(pendingContentKey(rawPending), rawPending);

  // A new batch starts with a clean slate: a stale refusal from the previous
  // one must not bleed onto it.
  const pendingKey = pending.map((group) => group.groupId).join(",");
  const { reset: resetAnswer } = answerMutation;
  useEffect(() => {
    resetAnswer();
  }, [pendingKey, resetAnswer]);

  // The just-sent prompt, shown beside the transcript until it lands in one for
  // real. See `./sending` for why it is never appended to the turns cache.
  const [echo, setEcho] = useState<SendEcho | null>(null);
  const queue = useWorkspaceQueue(workspaceId);
  const sending = echo && !echoLanded(echo, turns, queue.data) ? echo.text : null;
  // Cleared during render, not in an effect: an effect would leave the echo
  // painted for one extra frame beside the real message it duplicates, which is
  // exactly the flicker it exists to prevent.
  if (echo && sending === null) setEcho(null);

  const { mutate: sendMutate } = send;
  const onNew = useCallback(
    async (message: AppendMessage): Promise<void> => {
      const text = composerText(message);
      if (!text) return;
      setNotice(null);
      setEcho({ text, sentAt: new Date().toISOString() });
      sendMutate(text, {
        onError: (error) => {
          // The daemon refused it, so it is not in flight and never will be —
          // the notice below is the honest surface, not a row still claiming
          // to be on its way.
          setEcho(null);
          setNotice(refusalNotice(error, "send"));
        },
      });
    },
    [sendMutate],
  );

  const { mutate: interruptMutate } = interruptMutation;
  const onCancel = useCallback(async (): Promise<void> => {
    setNotice(null);
    interruptMutate(undefined, {
      onError: (error) => setNotice(refusalNotice(error, "interrupt")),
    });
  }, [interruptMutate]);

  const { mutate: answerMutate } = answerMutation;
  const answer = useCallback(
    (groupId: string, answers: QuestionAnswerItem[]): void => {
      if (!sessionId) return;
      setNotice(null);
      answerMutate(
        { sessionId, toolUseId: groupId, answers },
        { onError: (error) => setNotice(refusalNotice(error, "answer")) },
      );
    },
    [answerMutate, sessionId],
  );

  const threadList = useMemo(
    () =>
      onSwitchSession ? sessionThreadList(sessions, sessionId, onSwitchSession) : undefined,
    [sessions, sessionId, onSwitchSession],
  );

  const { runtime, messageCount } = useTranscriptRuntime({
    turns,
    onNew,
    onCancel,
    ...(threadList ? { threadList } : {}),
  });

  return {
    runtime,
    isEmpty: messageCount === 0 && pending.length === 0,
    pending,
    todo,
    notice,
    canInterrupt: agentIsWorking(snapshot, workspaceId, sessionId),
    interrupt: useCallback(() => void onCancel(), [onCancel]),
    answer,
    answering: answerMutation.isPending || answerMutation.isSuccess,
    hasEarlier: turnsQuery.hasEarlier,
    loadEarlier: turnsQuery.loadEarlier,
    loadingEarlier: turnsQuery.loadingEarlier,
    sending,
  };
}

/**
 * The read-only transcript, for a session with no workspace behind it.
 *
 * Structurally read-only rather than a disabled composer: a catalog session
 * usually cannot be steered at all, so offering a greyed-out input would promise
 * an affordance the wire cannot honour.
 */
export function useReadOnlyTranscript(turns: readonly SessionTurnView[] | undefined): {
  runtime: AssistantRuntime;
  isEmpty: boolean;
} {
  const { runtime, messageCount } = useTranscriptRuntime({ turns, readOnly: true });
  return { runtime, isEmpty: messageCount === 0 };
}

/** Grove's composer only ever emits text parts, so join and trim. */
function composerText(message: AppendMessage): string {
  return message.content
    .map((part) => (part.type === "text" ? part.text : ""))
    .join("")
    .trim();
}

/**
 * Recompute `value` cheaply on every render, but hand back the SAME
 * reference across renders whose `key` didn't change.
 *
 * `useMemo` can't do this alone: its cache key here would have to be `value`
 * itself, which is exactly the fresh-object-every-call problem this exists to
 * fix. Recomputing outside memoisation and comparing by a caller-supplied key
 * is the standard escape — cheap because the caller only reaches for this
 * when building the candidate value is cheap (a handful of pending questions,
 * not a transcript).
 */
export function useStableByKey<T>(key: string, value: T): T {
  const ref = useRef<{ key: string; value: T }>({ key, value });
  if (ref.current.key !== key) ref.current = { key, value };
  return ref.current.value;
}

/**
 * Everything about a pending batch that the reader can actually SEE.
 *
 * Exported and pure because it is the half of the stability fix that can be
 * wrong: `useStableByKey` is four lines that cannot really misbehave, but a
 * key that omits a field the card renders would pin a STALE card on screen
 * for the whole life of the question — a silent wrong answer, not a crash.
 *
 * `questions` is deliberately not in the key. `presentation` is derived from
 * it by `questionPresentation`, so any change to the questions that reaches
 * the screen reaches it through there; keying on the raw questions as well
 * would only re-admit the per-tick churn this exists to stop.
 */
export function pendingContentKey(groups: readonly PendingQuestionGroup[]): string {
  return JSON.stringify(groups.map(({ groupId, presentation }) => ({ groupId, presentation })));
}

function agentIsWorking(
  snapshot: DashboardSnapshotView | null,
  workspaceId: string,
  sessionId: string | null,
): boolean {
  if (!sessionId) return false;
  for (const project of snapshot?.projects ?? []) {
    for (const workspace of project.workspaces) {
      if (workspace.state.id !== workspaceId) continue;
      const match = workspace.sessions.find((entry) => entry.session.session_id === sessionId);
      return match?.activity.state === "working";
    }
  }
  return false;
}
