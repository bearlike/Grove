"use client";

import { useMemo } from "react";
import { AssistantRuntimeProvider } from "@assistant-ui/react";

import { ErrorState } from "@/components/elements/error-state";
import { useActivityStream, useSessionTurns, useWorkspaceQueue } from "@/lib/grove/hooks";
import type { WorkspaceQueueView } from "@/lib/grove/api";
import type { GroveThreadState } from "@/lib/grove/runtime";
import { GroveDataParts } from "./data-parts";
import { PendingQuestion } from "./pending-question";
import { QueuePanel } from "./queue-panel";
import { SessionPicker } from "./session-picker";
import { Thread, type ThreadComponents } from "./thread";
import { THREAD_WIDTH } from "./thread-width";
import { TodoPanel } from "./todo-panel";
import { GROVE_THREAD_COMPONENTS } from "./tool-call-part";
import { TranscriptSkeleton } from "./transcript-skeleton";

/**
 * `GROVE_THREAD_COMPONENTS`, with the vendored "How can I help you today?"
 * welcome suppressed.
 *
 * Measured on a 1,100-turn transcript: even once the react-query gate above
 * only mounts `Thread` after `turns` has genuinely resolved, `Thread`'s own
 * `isNewChatView` reads assistant-ui's INTERNAL runtime state
 * (`useAuiState`), which syncs from the external store's `messages` prop via
 * an effect — not synchronously on the mount that receives it. On a large
 * transcript that sync visibly lags: the welcome screen (and, alongside it,
 * the composer) rendered for a further ~2 s after the turns query had already
 * resolved with 1,163 real messages. `thread.isEmpty` has no such lag — it is
 * derived straight from the same `turns` data in the same render, never from
 * the runtime's own copy — so it is what decides whether the real welcome may
 * show, and the vendored one is suppressed whenever it would disagree.
 */
const SUPPRESSED_WELCOME_COMPONENTS: ThreadComponents = {
  ...GROVE_THREAD_COMPONENTS,
  Welcome: () => null,
};

/**
 * The conversation with the agent: transcript, composer, plan and live
 * questions.
 *
 * The thread state is OWNED BY THE PAGE, not by this component. The agent's
 * status and its interrupt belong in the one header row, and the page is what
 * renders that header — so lifting the hook up is what let four stacked rows
 * become one. What is left here is rendering.
 *
 * A live question is still a SIBLING of the message stream, never a message:
 * the message list keys children by position, so an interactive card at the
 * tail would remount — dropping a half-made selection — every time the
 * transcript grows under it. The plan card, by contrast, now rides INSIDE the
 * thread's viewport footer, which is stable chrome for the same reason the
 * composer is.
 *
 * THE THREE-STATE GATE. `useSessionTurns` and `useActivityStream` are read
 * here a SECOND time — `useGroveThread` (in the parent) already reads the
 * former to build `thread.runtime`, and `Workspace` already reads the latter
 * for `sessionId` — but both are `useQuery`/context reads keyed identically,
 * so this costs no extra request; it only asks the cache "have you actually
 * answered yet". Reading `thread.runtime`'s own message count cannot answer
 * that: an unresolved query and a genuinely empty one both hand back zero
 * messages, so gating on the runtime is the "no data vs. no data yet"
 * conflation this component exists to remove — the loading screen becomes
 * `Thread`'s own "How can I help you today?" welcome, WITH a composer,
 * because a thread of zero messages looks exactly like a new one. `pending` →
 * a skeleton shaped like a conversation; `error` → the same honest surface
 * `Workspace` already uses for its own query; `success` → the real thread,
 * where the empty/new-session welcome is now finally reachable ONLY once the
 * query has resolved empty for real.
 */
export function Transcript({
  workspaceId,
  sessionId,
  thread,
  narrow,
}: {
  workspaceId: string;
  sessionId: string | null;
  thread: GroveThreadState;
  /** True in split view, where the pane is a half-width column. */
  narrow: boolean;
}) {
  // Called unconditionally, ahead of the early returns below, per the rules of
  // hooks — disabled (via `null`) rather than skipped while there is no
  // session to steer, since `enabled` is exactly what these hooks already gate
  // their fetch on.
  const queue = useWorkspaceQueue(sessionId === null ? null : workspaceId);
  // `.query`: this component only needs the ordinary loading/error/data state
  // for its three-state gate below — `hasEarlier`/`loadEarlier` are read off
  // `thread` (from `useGroveThread` in the parent), the same split every other
  // duplicate read on this pane already follows.
  const turns = useSessionTurns(workspaceId, sessionId).query;
  const activity = useActivityStream();

  // No resolvable session means there is no transcript to render at all — the
  // only useful thing this pane can do is offer the remap that fixes it. But
  // `sessionId` is ITSELF derived from the activity snapshot, which is a query
  // too: reading `null` before that snapshot has ever arrived is "no session
  // yet", not "no session", and the picker's own empty copy ("No agent session
  // is attributed") is exactly the wrong-but-final claim this gate exists to
  // prevent — same defect as the welcome screen below, one layer up.
  if (sessionId === null) {
    if (activity.isPending) return <TranscriptSkeleton />;
    return <SessionPicker workspaceId={workspaceId} resolvedSessionId={sessionId} />;
  }

  if (turns.isPending) return <TranscriptSkeleton />;
  if (turns.isError) {
    return (
      <ErrorState
        className="m-4"
        title="Couldn’t load this transcript"
        detail={turns.error.message}
        retrying={turns.isFetching}
        onRetry={() => void turns.refetch()}
      />
    );
  }

  return (
    <AssistantRuntimeProvider runtime={thread.runtime}>
      <GroveDataParts />
      <div className="flex min-h-0 min-w-0 flex-1 flex-col" data-testid="transcript">
        <ThreadPane thread={thread} narrow={narrow} queue={queue.data ?? null} />
      </div>
    </AssistantRuntimeProvider>
  );
}

/** An empty batch is the overwhelmingly common case, and a fresh `[]` on every
 * frame would defeat the memo below on its most valuable path. */
const NO_PENDING: GroveThreadState["pending"] = [];

/**
 * The thread, held STILL while the fleet moves under it.
 *
 * The page re-renders about once a second, because it reads the activity
 * snapshot and every `session_activity` frame replaces it — and `useGroveThread`
 * hands back a fresh object each time, so no `React.memo` on this component
 * could ever hit.
 *
 * Returning a MEMOISED ELEMENT is what fixes it: React skips a subtree whose
 * element is referentially unchanged, so the thread re-renders only when one of
 * the things it actually shows moves. Measured over a 30 s window of one frame
 * per second, on a six-turn transcript — scripting 3.65 s → 2.60 s, layouts
 * 60 → 0, style recalculations 247 → 89. Every one of those layouts was the
 * browser re-measuring a tree whose content had not changed.
 *
 * It stays live regardless: everything inside subscribes to the assistant-ui
 * runtime itself, which is the whole point of an external store, and the
 * parent's render was never what delivered a new message.
 */
function ThreadPane({
  thread,
  narrow,
  queue,
}: {
  thread: GroveThreadState;
  narrow: boolean;
  /** The workspace's steer queue, or null while it has not loaded yet. */
  queue: WorkspaceQueueView | null;
}) {
  const { todo, answering, answer, notice, isEmpty, hasEarlier, loadEarlier, loadingEarlier, sending } =
    thread;
  const pending = thread.pending.length === 0 ? NO_PENDING : thread.pending;
  const maxWidth = narrow ? THREAD_WIDTH.split : THREAD_WIDTH.full;

  return useMemo(
    () => (
      <Thread
        components={isEmpty ? GROVE_THREAD_COMPONENTS : SUPPRESSED_WELCOME_COMPONENTS}
        maxWidth={maxWidth}
        hasEarlier={hasEarlier}
        loadingEarlier={loadingEarlier}
        onLoadEarlier={loadEarlier}
        footer={
          <>
            {/* FIRST in the footer, because it is the newest thing that
                happened and it sits where the next message would appear.
                It clears the instant the transcript or the queue shows the
                same text for real, so it never doubles either of them. */}
            {sending && <SendingEcho text={sending} />}
            {todo && <TodoPanel todo={todo} />}
            {queue && <QueuePanel queue={queue} />}
            {pending.map((group) => (
              <PendingQuestion
                key={group.groupId}
                group={group}
                submitting={answering}
                onSubmit={answer}
              />
            ))}
            {notice && (
              <p role="status" className="text-xs" data-testid="chat-notice">
                {notice}
              </p>
            )}
          </>
        }
      />
    ),
    [
      maxWidth,
      narrow,
      todo,
      queue,
      pending,
      answering,
      answer,
      notice,
      isEmpty,
      hasEarlier,
      loadEarlier,
      loadingEarlier,
      sending,
    ],
  );
}

/**
 * The prompt you just sent, until it shows up for real.
 *
 * Deliberately the plainest thing in the footer — one muted line, no card, no
 * spinner. It is not an event worth a surface of its own; it exists only so the
 * couple of seconds between pressing send and the daemon reporting the turn are
 * not silent. Anything heavier would then have to animate OUT, and a card that
 * appears and vanishes under every message is worse than the wait it covers.
 *
 * `role="status"` rather than `aria-live="assertive"`: it is a courtesy
 * confirmation, not something that should interrupt what a screen reader is
 * already saying.
 */
function SendingEcho({ text }: { text: string }) {
  return (
    <p
      role="status"
      data-testid="sending-echo"
      className="text-xs text-content-tertiary italic"
    >
      Sending: {text}
    </p>
  );
}
