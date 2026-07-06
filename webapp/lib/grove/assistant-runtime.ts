"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  useExternalStoreRuntime,
  type AppendMessage,
  type AssistantRuntime,
} from "@assistant-ui/react";
import {
  chatItemToThreadMessage,
  chatItemsFromTurns,
  type ChatItem,
  type PendingQuestionGroup,
} from "./chat-turns";
import { livePendingQuestions } from "./live-question";
import { refusalNotice } from "./steering-notice";
import { useAnswerQuestion, useInterrupt, useSendMessage, useSessionTurns } from "./hooks";
import type { QuestionAnswerItem, QuestionInteraction } from "./question-plan";
import type { AgentActivityState, DashboardSnapshotView } from "./types";

/** Chat-tier transcript cadence — a conversation surface someone is watching. */
const CHAT_TURNS_REFETCH_MS = 5_000;

/** Stable no-op for the `setMessages` seam (branching is out of scope, v1). */
const NOOP = () => {};

/** Everything `ChatPanel` renders around the assistant-ui transcript, resolved
 * from Grove's existing SSE + TanStack pipeline. `runtime` drives the thread;
 * the rest are the affordances assistant-ui doesn't own (Grove steers a WORKING
 * agent, so the interrupt + refusal notice live outside the composer state). */
export interface GroveChatState {
  runtime: AssistantRuntime;
  /** True when the transcript AND the live pending group are both empty — the
   * signal for the empty state. */
  isEmpty: boolean;
  /** The LIVE pending question group, rendered as a sibling of the message
   * stream (never a message — it would remount on transcript growth). */
  pending: PendingQuestionGroup | null;
  /** A steering refusal (send / interrupt), rendered as a quiet inline notice. */
  notice: string | null;
  /** The agent is WORKING — the emergency interrupt is offered only then. */
  canInterrupt: boolean;
  /** Fire the interrupt (POST /interrupt); a 409/501 becomes `notice`. */
  onInterrupt: () => void;
}

/** Pull the plain text out of a composer submission — Grove's composer only ever
 * emits text parts, so join them and trim. */
function appendText(message: AppendMessage): string {
  return message.content
    .map((part) => (part.type === "text" ? part.text : ""))
    .join("")
    .trim();
}

/**
 * Wrap Grove's existing transcript pipeline (`useSessionTurns` +
 * `chatItemsFromTurns` + `livePendingQuestions` + `useSendMessage` /
 * `useInterrupt` / `useAnswerQuestion`) into an assistant-ui external-store
 * runtime. The store's messages ARE Grove's `ChatItem[]`; `convertMessage`
 * (the pure `chatItemToThreadMessage`, with a per-turn `createdAt` layered on)
 * turns each into a `ThreadMessageLike`. Sending routes through `onNew`, which
 * fires Grove's optimistic `useSendMessage` (the store never appends the sent
 * message itself — it reflects `messages`, which the optimistic cache write
 * grows).
 *
 * **`isRunning` is deliberately NOT set on the adapter.** assistant-ui gates the
 * composer's send on `thread.isRunning` (`useComposerSend` disables it while
 * running unless a queue adapter is present) and injects a synthetic streaming
 * placeholder after the last user message — both fight Grove's core model, where
 * you steer a WORKING agent with follow-up messages. So the working state is
 * sourced from the `agentState` prop instead (the interrupt gate + the panel's
 * shimmer), and every mapped assistant message pins `status: complete` so
 * `thread.isRunning` stays false and the composer stays sendable. Autoscroll is
 * unaffected — the viewport reacts to rendered height, not to a run flag.
 */
export function useGroveChatRuntime({
  workspaceId,
  sessionId,
  snapshot,
  agentState,
}: {
  workspaceId: string;
  sessionId: string | null;
  snapshot: DashboardSnapshotView | null;
  agentState: AgentActivityState;
}): GroveChatState {
  const { data: detail } = useSessionTurns(workspaceId, sessionId, CHAT_TURNS_REFETCH_MS);
  const send = useSendMessage(workspaceId, sessionId);
  const interrupt = useInterrupt(workspaceId);

  // Memoize the pending group by SNAPSHOT identity: `livePendingQuestions`
  // filters a fresh array each call, and the external store re-processes (and
  // remounts the question card, detaching its buttons mid-interaction) whenever
  // the `messages` identity churns. Stable inputs → the store reconciles in
  // place instead of thrashing.
  const liveQuestions = useMemo(
    () => livePendingQuestions(snapshot, workspaceId, sessionId),
    [snapshot, workspaceId, sessionId],
  );
  const answerQuestion = useAnswerQuestion(workspaceId);
  const [questionNotice, setQuestionNotice] = useState<string | null>(null);
  // A new pending group (a different group_id) starts with a clean slate — a
  // stale error from the PREVIOUS group must not bleed onto it.
  const liveGroupId = liveQuestions[0]?.group_id ?? null;
  useEffect(() => {
    setQuestionNotice(null);
    answerQuestion.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveGroupId]);

  const [notice, setNotice] = useState<string | null>(null);

  // Turn heads carry the timestamp; `chatItemsFromTurns` flattens and drops it,
  // so re-run it PER TURN (a tool run never spans turns, so per-turn == whole)
  // and tag each turn's FIRST item with that turn's `started_at`. The parallel
  // `startedAts` array aligns to `items` by index; `convertMessage` reads it.
  const turns = detail?.turns;
  const base = useMemo(() => {
    const items: ChatItem[] = [];
    const startedAts: (string | null)[] = [];
    for (const turn of turns ?? []) {
      chatItemsFromTurns([turn]).forEach((item, i) => {
        items.push(item);
        startedAts.push(i === 0 ? turn.started_at : null);
      });
    }
    return { items, startedAts };
  }, [turns]);

  // The live pending group's answer controls. Memoized so its identity (and
  // therefore the pending message's) is stable while the group + submit state
  // are unchanged — the card stays mounted and clickable until something real
  // changes. `answerQuestion.mutate` is a stable TanStack reference.
  const { mutate: answerMutate } = answerQuestion;
  const interactive = useMemo<QuestionInteraction>(
    () => ({
      submitting: answerQuestion.isPending || answerQuestion.isSuccess,
      error: questionNotice,
      onSubmit: (answers: QuestionAnswerItem[]) => {
        if (!sessionId || !liveGroupId) return;
        setQuestionNotice(null);
        answerMutate(
          { sessionId, toolUseId: liveGroupId, answers },
          { onError: (err) => setQuestionNotice(refusalNotice(err, "answer")) },
        );
      },
    }),
    [answerQuestion.isPending, answerQuestion.isSuccess, questionNotice, sessionId, liveGroupId, answerMutate],
  );

  // The live pending group renders as a sibling of the message stream, NOT a
  // message — `ThreadPrimitive.Messages` keys by index, so a card at the tail
  // would remount (and drop in-flight selections) every time the transcript
  // grows under it (research §5 / the index-key trap).
  const pending: PendingQuestionGroup | null =
    liveQuestions.length > 0 ? { questions: liveQuestions, interactive } : null;

  const convertMessage = useCallback(
    (item: ChatItem, index: number) => {
      const message = chatItemToThreadMessage(item, index);
      const startedAt = base.startedAts[index];
      if (!startedAt) return message;
      // The turn head's real timestamp. assistant-ui defaults `createdAt` to
      // now() when unset, so it can't be told from a real one — the panel reads
      // `custom.startedAt` (present ONLY on heads) to render it exactly there.
      return {
        ...message,
        createdAt: new Date(startedAt),
        metadata: { custom: { ...(message.metadata?.custom ?? {}), startedAt } },
      };
    },
    [base.startedAts],
  );

  const { mutate: sendMutate } = send;
  const onNew = useCallback(
    async (message: AppendMessage) => {
      const text = appendText(message);
      if (!text) return;
      setNotice(null);
      sendMutate(text, { onError: (err) => setNotice(refusalNotice(err, "send")) });
    },
    [sendMutate],
  );

  const { mutate: interruptMutate } = interrupt;
  const onCancel = useCallback(async () => {
    setNotice(null);
    interruptMutate(undefined, {
      onError: (err) => setNotice(refusalNotice(err, "interrupt")),
    });
  }, [interruptMutate]);

  const runtime = useExternalStoreRuntime<ChatItem>({
    messages: base.items,
    convertMessage,
    onNew,
    onCancel,
    // Branch switching is out of scope (v1) — the store still requires the seam.
    setMessages: NOOP,
  });

  const onInterrupt = useCallback(() => void onCancel(), [onCancel]);

  return {
    runtime,
    // Empty state only when there is no transcript AND no live question.
    isEmpty: base.items.length === 0 && pending === null,
    pending,
    notice,
    // Mirror the dashboard live-toggle: interrupting an idle agent is a 409.
    canInterrupt: agentState === "working",
    onInterrupt,
  };
}
