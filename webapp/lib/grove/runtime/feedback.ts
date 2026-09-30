"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
} from "react";
import type { FeedbackAdapter, ThreadMessageLike } from "@assistant-ui/react";

import { GROVE_TURN_ANSWER } from "@/lib/grove/adapters";
import type { TurnFeedbackRequest } from "@/lib/grove/api";
import { groveClient } from "@/lib/grove/hooks/client";
import { refusalNotice } from "./notice";

export type Rating = TurnFeedbackRequest["rating"];

/** The reasons each verdict may name — two rubrics, never one list. */
export type FeedbackReasons = Readonly<Record<Rating, readonly string[]>>;

/**
 * What the thread's action bar needs beyond assistant-ui's own feedback
 * primitives: the reasons each thumb may name, and a way to send them.
 * `null` wherever ratings are off, which is also what hides the thumbs.
 */
export interface TurnFeedbackControls {
  reasons: FeedbackReasons;
  /** Send the full rating for one turn; resolves once the daemon recorded it. */
  submit: (
    startedAt: string,
    request: Omit<TurnFeedbackRequest, "started_at">,
  ) => Promise<void>;
}

export const TurnFeedbackContext = createContext<TurnFeedbackControls | null>(
  null,
);

export function useTurnFeedbackControls(): TurnFeedbackControls | null {
  return useContext(TurnFeedbackContext);
}

/** The turn a message answers, or `null` when it carries no action bar. */
export function turnAnswerOf(custom: Record<string, unknown>): string | null {
  const startedAt = custom[GROVE_TURN_ANSWER];
  return typeof startedAt === "string" ? startedAt : null;
}

/**
 * Ratings for one session's turns, wired into assistant-ui's native feedback.
 *
 * assistant-ui keeps a submitted vote on the message inside ITS repository, and
 * Grove re-supplies every message from the transcript on each poll, so a vote
 * kept only there is erased by the next tick. The votes therefore live here,
 * keyed by the turn's `started_at`, and are stamped back onto the answer
 * messages as `submittedFeedback` — which is what the vendored
 * `ActionBarPrimitive.Feedback*` read to show the pressed state.
 *
 * An empty `negative` list means Langfuse is not configured
 * (`WhoamiView.feedback_reasons`), and then no adapter is returned, so
 * assistant-ui renders no thumbs at all.
 */
export function useTurnFeedback(
  workspaceId: string,
  sessionId: string | null,
  reasons: FeedbackReasons,
  onRefused: (notice: string) => void,
): {
  adapter: FeedbackAdapter | undefined;
  controls: TurnFeedbackControls | null;
  stamp: (messages: ThreadMessageLike[]) => ThreadMessageLike[];
} {
  const [votes, setVotes] = useState<ReadonlyMap<string, Rating>>(
    () => new Map(),
  );
  const enabled = sessionId !== null && reasons.negative.length > 0;

  const submit = useCallback(
    async (
      startedAt: string,
      request: Omit<TurnFeedbackRequest, "started_at">,
    ): Promise<void> => {
      if (!sessionId) return;
      setVotes((current) => new Map(current).set(startedAt, request.rating));
      try {
        await groveClient.submitTurnFeedback(workspaceId, sessionId, {
          started_at: startedAt,
          ...request,
        });
      } catch (error) {
        onRefused(refusalNotice(error, "rate"));
        throw error;
      }
    },
    [workspaceId, sessionId, onRefused],
  );

  const adapter = useMemo<FeedbackAdapter | undefined>(
    () =>
      enabled
        ? {
            submit: ({ message, type }) => {
              const startedAt = turnAnswerOf(message.metadata.custom);
              // A quick thumb records the verdict at once; its reasons and
              // note follow from the dialog as a second submit for the same
              // turn, which the daemon treats as a replacement.
              if (startedAt)
                void submit(startedAt, { rating: type }).catch(() => {});
            },
          }
        : undefined,
    [enabled, submit],
  );

  const controls = useMemo(
    () => (enabled ? { reasons, submit } : null),
    [enabled, reasons, submit],
  );

  const stamp = useCallback(
    (messages: ThreadMessageLike[]): ThreadMessageLike[] =>
      votes.size === 0
        ? messages
        : messages.map((message) => {
            const startedAt =
              message.metadata?.custom && turnAnswerOf(message.metadata.custom);
            const type = startedAt ? votes.get(startedAt) : undefined;
            return type
              ? {
                  ...message,
                  metadata: {
                    ...message.metadata,
                    submittedFeedback: { type },
                  },
                }
              : message;
          }),
    [votes],
  );

  return { adapter, controls, stamp };
}
