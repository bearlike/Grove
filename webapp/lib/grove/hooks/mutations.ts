"use client";

import { useCallback } from "react";
import {
  useMutation,
  useQueryClient,
  type QueryClient,
  type UseMutationResult,
} from "@tanstack/react-query";

import type {
  CreateWorkspaceRequest,
  QuestionAnswerItem,
  WorkspaceQueueView,
  WorkspaceStateView,
} from "@/lib/grove/api";
import { groveClient } from "./client";
import { groveKeys } from "./keys";

/**
 * Write hooks over the daemon.
 *
 * Engine lifecycle semantics are never re-implemented here: a verb the engine
 * would refuse simply surfaces its typed `GroveProtocolError`. These hooks only
 * fire the request and decide what to re-read afterwards.
 *
 * No optimistic update for lifecycle — the re-fetch is the source of truth, and
 * a workspace that half-transitions is exactly the case a guessed state hides.
 * Steering is the one exception (see `useSendMessage`), because a prompt that
 * vanishes for a poll interval reads as a dropped message.
 */

/** Everything that changes when a workspace's lifecycle moves. */
function invalidateWorkspace(queryClient: QueryClient, id: string): void {
  void queryClient.invalidateQueries({ queryKey: groveKeys.workspaces });
  void queryClient.invalidateQueries({ queryKey: groveKeys.activity });
  void queryClient.invalidateQueries({ queryKey: groveKeys.workspace(id) });
}

export interface WorkspaceActions {
  pause: UseMutationResult<WorkspaceStateView, Error, { force?: boolean } | void>;
  resume: UseMutationResult<WorkspaceStateView, Error, void>;
  respawn: UseMutationResult<WorkspaceStateView, Error, void>;
  kill: UseMutationResult<void, Error, { deleteBranch: boolean | null }>;
}

/**
 * The four lifecycle verbs, bundled because they refresh identically.
 *
 * `kill`'s `deleteBranch` is required and nullable rather than defaulted: null
 * means "let the engine resolve it from branch provenance", which is a
 * different decision from an explicit false, and the caller must have made one.
 */
export function useWorkspaceActions(id: string): WorkspaceActions {
  const queryClient = useQueryClient();
  const settle = useCallback(() => invalidateWorkspace(queryClient, id), [queryClient, id]);

  return {
    pause: useMutation({
      mutationFn: (options: { force?: boolean } | void) =>
        groveClient.pauseWorkspace(id, options?.force ?? false),
      onSuccess: settle,
    }),
    resume: useMutation({
      mutationFn: () => groveClient.resumeWorkspace(id),
      onSuccess: settle,
    }),
    respawn: useMutation({
      mutationFn: () => groveClient.respawnWorkspace(id),
      onSuccess: settle,
    }),
    kill: useMutation({
      mutationFn: ({ deleteBranch }: { deleteBranch: boolean | null }) =>
        groveClient.killWorkspace(id, deleteBranch),
      onSuccess: settle,
    }),
  };
}

export function useCreateWorkspace(): UseMutationResult<
  WorkspaceStateView,
  Error,
  CreateWorkspaceRequest
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: CreateWorkspaceRequest) => groveClient.createWorkspace(request),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: groveKeys.workspaces });
      void queryClient.invalidateQueries({ queryKey: groveKeys.activity });
    },
  });
}

/**
 * Re-point a workspace at a different agent session.
 *
 * Never read the pinned id back off the response body — invalidate the activity
 * snapshot and let the engine's own ordering (which places the pinned session
 * first) be the answer, or the pin silently un-pins on the next load.
 */
export function useRemapSession(
  id: string,
): UseMutationResult<WorkspaceStateView, Error, string> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (sessionRef: string) => groveClient.remapSession(id, sessionRef),
    onSuccess: () => {
      invalidateWorkspace(queryClient, id);
      void queryClient.invalidateQueries({ queryKey: groveKeys.sessions(id) });
    },
  });
}

/**
 * Steer a running agent with a follow-up prompt.
 *
 * FIXED BUG: this used to optimistically append the prompt as a new transcript
 * turn. That target deletes itself the moment the agent is busy — the refetch
 * this triggers asks the daemon for `after_turn = held.length - 1` while the
 * server still holds `held.length` turns, `mergeTurns` computes a window of
 * zero new turns, and the merge falls back to `held.slice(0, N)` with N turns,
 * silently dropping the optimistic one. The message flashed and vanished, and
 * whether you saw it at all depended only on how busy the agent happened to be.
 *
 * The honest optimistic target is the QUEUE, not the transcript: a message sent
 * to a busy agent genuinely is queued by the harness, and that is what the
 * queue card (`QueuePanel`) now shows. Nothing is appended when the queue has
 * never been fetched (nothing cached to append to) or the harness's queue is
 * one Grove cannot see (`supported: false`) — in both cases there is no honest
 * optimistic row to draw, and the settle-time invalidation is what corrects an
 * agent that was actually idle and processed the message immediately instead of
 * queueing it.
 */
export function useSendMessage(
  workspaceId: string,
): UseMutationResult<void, Error, string, WorkspaceQueueView | undefined> {
  const queryClient = useQueryClient();
  const key = groveKeys.queue(workspaceId);

  return useMutation({
    mutationFn: (text: string) => groveClient.sendMessage(workspaceId, text),
    onMutate: async (text: string) => {
      await queryClient.cancelQueries({ queryKey: key });
      const previous = queryClient.getQueryData<WorkspaceQueueView>(key);
      const next = withOptimisticSend(previous, text, new Date().toISOString());
      if (next) queryClient.setQueryData<WorkspaceQueueView>(key, next);
      return previous;
    },
    onError: (_error, _text, previous) => {
      if (previous) queryClient.setQueryData(key, previous);
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: key });
      void queryClient.invalidateQueries({ queryKey: groveKeys.activity });
    },
  });
}

/**
 * The queue cache after optimistically appending one just-sent message.
 *
 * Pulled out as a plain function of its arguments so the actual decision here
 * — append only when there is a cached queue AND it is one the harness lets
 * Grove see — is exercisable without a query client or a DOM, the same reason
 * the pure adapters in `lib/grove/adapters` hold no React. Returns the
 * argument unchanged (including `undefined`) rather than `null` when there is
 * nothing honest to draw, so a caller can tell "no optimistic row" from "wrote
 * one" by identity.
 */
export function withOptimisticSend(
  previous: WorkspaceQueueView | undefined,
  text: string,
  sentAt: string,
): WorkspaceQueueView | undefined {
  if (!previous?.supported) return previous;
  return {
    ...previous,
    messages: [
      ...previous.messages,
      { text, sent_at: sentAt, position: previous.messages.length },
    ],
  };
}

/** Interrupt a working agent. A 409 means it was already idle. */
export function useInterrupt(workspaceId: string): UseMutationResult<void, Error, void> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => groveClient.interrupt(workspaceId),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: groveKeys.activity });
    },
  });
}

export interface AnswerQuestionInput {
  sessionId: string;
  /** The batch's `group_id` — one ask is answered atomically, never per question. */
  toolUseId: string;
  answers: QuestionAnswerItem[];
}

/**
 * Answer a live question batch.
 *
 * A 409 means the question resolved in the terminal first: show the refusal,
 * degrade the card to read-only, and let the stream confirm.
 */
export function useAnswerQuestion(
  workspaceId: string,
): UseMutationResult<void, Error, AnswerQuestionInput> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ sessionId, toolUseId, answers }: AnswerQuestionInput) =>
      groveClient.answerQuestion(workspaceId, sessionId, toolUseId, answers),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: groveKeys.activity });
    },
  });
}

/** Fire a slash command or skill into the running session. */
export function useInvokeControl(workspaceId: string): UseMutationResult<void, Error, string> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => groveClient.invokeControl(workspaceId, name),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: groveKeys.activity });
    },
  });
}

/** Switch the running session's model. Grove forwards the id verbatim and never
 * interprets it — the catalog is a hint, never a closed set. */
export function useSwitchModel(workspaceId: string): UseMutationResult<void, Error, string> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (model: string) => groveClient.switchModel(workspaceId, model),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: groveKeys.controls(workspaceId) });
    },
  });
}
