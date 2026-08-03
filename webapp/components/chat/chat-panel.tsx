"use client";

import type { CSSProperties, ReactNode } from "react";
import { ArrowDownIcon, ArrowUpIcon, MessagesSquare, SquareIcon } from "lucide-react";
import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  ThreadPrimitive,
} from "@assistant-ui/react";
import { ErrorBoundary } from "@/components/error-boundary";
import { GroveMessage } from "@/components/chat/chat-message";
import { TodoListCard } from "@/components/chat/todo-list-view";
import { PendingQuestionCard } from "@/components/workspace/question-card";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useGroveChatRuntime } from "@/lib/grove/assistant-runtime";
import type { AgentActivityState, DashboardSnapshotView } from "@/lib/grove/types";

/**
 * The steer-capable chat surface on `/w/[id]`: the latest session's transcript
 * rendered through the headless `ThreadPrimitive` / `MessagePrimitive` /
 * `ComposerPrimitive`, over Grove's own external-store runtime
 * (`useGroveChatRuntime`, which wraps the unchanged `useSessionTurns` +
 * `chatItemsFromTurns` + SSE pending-question pipeline). Heaviest leaf on the
 * page (streamdown + assistant-ui) — the page loads it via `next/dynamic`, so
 * always import this module lazily.
 *
 * A LIVE pending `AskUserQuestion` GROUP is sourced from the SSE activity
 * `snapshot` (page-owned) and appended as one more message (keyed by
 * group_id), rendered by the same `PendingQuestionCard`. The panel does not
 * OWN session selection — the page hands down the resolved `sessionId`, the
 * activity `snapshot`, and the selected session's `agentState` — this is a
 * pure renderer of that one session's transcript + composer.
 *
 * Test seams: `chat-panel`, `chat-message` + `data-role` (each carrying a
 * `role-label` speaker tag), `tool-group`, `chat-tool` (inside an expanded
 * group), `chat-notification`, `chat-question` (read-only card OR the live
 * interactive `pending-question-card`), `chat-composer`, `chat-interrupt`,
 * `chat-notice`, `user-message-collapse`/`user-message-toggle`.
 */
export function ChatPanel({
  workspaceId,
  sessionId,
  snapshot,
  agentState,
  emptyStatePicker,
  wide,
}: {
  workspaceId: string;
  /** The page-selected session whose transcript this panel renders. */
  sessionId: string | null;
  /** The live activity snapshot (page-owned) read for pending questions. */
  snapshot: DashboardSnapshotView | null;
  /** The selected session's agent state — gates the WORKING-only interrupt. */
  agentState: AgentActivityState;
  /**
   * The page-wired track picker. Present ONLY when the workspace tracks no
   * usable session but ungated candidates exist — its presence flips the empty
   * state from the generic "send a message" copy to a "pick a session to follow"
   * CTA. Absent ⇒ the generic empty state.
   */
  emptyStatePicker?: ReactNode;
  /**
   * True when the transcript is the ONLY pane on screen (single-pane view, no
   * work panel sharing the row) — widens the column measure since there's no
   * neighboring pane to leave room for. False (or omitted) keeps the Claude
   * reading measure used inside the split view. See `THREAD_MAX_WIDTH_WIDE`.
   */
  wide?: boolean;
}) {
  // Self-wrapped boundary: a malformed streamed turn degrades to one
  // placeholder tile, never a white-screened detail page.
  return (
    <ErrorBoundary>
      <ChatPanelInner
        workspaceId={workspaceId}
        sessionId={sessionId}
        snapshot={snapshot}
        agentState={agentState}
        emptyStatePicker={emptyStatePicker}
        wide={wide}
      />
    </ErrorBoundary>
  );
}

/**
 * Thread layout tokens from assistant-ui's own styled template, set on
 * the panel root so every descendant (the column measure, the composer) reads
 * one source. `--thread-max-width` is the Claude reading measure (44rem/704px)
 * while the transcript shares the row with the work panel (split view); with
 * no pane to share the row against (single-pane transcript view), it widens to
 * `THREAD_MAX_WIDTH_WIDE` so the column actually uses the freed-up width
 * instead of leaving it as empty gutters either side. The composer trio drives
 * its floating-input look (a muted-tinted plane at the 24px composer radius).
 */
const THREAD_MAX_WIDTH = "44rem";
const THREAD_MAX_WIDTH_WIDE = "80rem";

function threadTokens(wide: boolean): CSSProperties {
  return {
    "--thread-max-width": wide ? THREAD_MAX_WIDTH_WIDE : THREAD_MAX_WIDTH,
    "--composer-bg": "color-mix(in oklab, var(--color-muted) 30%, var(--color-background))",
    "--composer-radius": "1.5rem",
    "--composer-padding": "8px",
  } as CSSProperties;
}

/**
 * The one shared column measure (LibreChat's no-drift rule): transcript
 * content, notice, and composer all mount inside it so they stay column-aligned
 * at every width. `px-3` makes mobile effectively edge-to-edge; the max-width is
 * the `--thread-max-width` token, set per-pane-mode by `threadTokens()`.
 */
const CHAT_COLUMN = "mx-auto w-full max-w-[var(--thread-max-width)] px-3 sm:px-4 lg:px-6";

function ChatPanelInner({
  workspaceId,
  sessionId,
  snapshot,
  agentState,
  emptyStatePicker,
  wide,
}: {
  workspaceId: string;
  sessionId: string | null;
  snapshot: DashboardSnapshotView | null;
  agentState: AgentActivityState;
  emptyStatePicker?: ReactNode;
  wide?: boolean;
}) {
  const { runtime, isEmpty, pending, todo, notice, canInterrupt, onInterrupt } =
    useGroveChatRuntime({
      workspaceId,
      sessionId,
      snapshot,
      agentState,
    });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <div
        className="flex min-h-0 min-w-0 flex-1 flex-col gap-2"
        style={threadTokens(Boolean(wide))}
        data-testid="chat-panel"
      >
        {/* The Viewport is itself the scroll container (assistant-ui drives
            scrollTop on it directly) and hosts autoscroll — `role="log"` is the
            Playwright scroll seam. `min-w-0` + break-words in the parts keep a
            wide transcript scrolling inside its pane, never widening the page. */}
        <ThreadPrimitive.Viewport
          role="log"
          className="relative min-h-0 min-w-0 flex-1 overflow-y-auto"
        >
          <div className={cn(CHAT_COLUMN, "flex min-w-0 flex-col py-3")}>
            {isEmpty ? (
              <EmptyState picker={emptyStatePicker} />
            ) : (
              <ThreadPrimitive.Messages components={{ Message: GroveMessage }} />
            )}
            {/* The LIVE pending question card — a sibling of the message stream,
                keyed by group_id so a NEW group remounts (fresh selections) while
                the same group stays put across transcript growth (assistant-ui's
                Messages key by index, so a card AS a message would thrash). */}
            {pending && (
              <div
                key={pending.questions[0]?.group_id ?? "pending"}
                data-testid="chat-question"
                className="mt-3 w-full min-w-0"
              >
                <PendingQuestionCard
                  questions={pending.questions}
                  onSubmit={pending.interactive.onSubmit}
                  submitting={pending.interactive.submitting}
                  error={pending.interactive.error}
                />
              </div>
            )}
            {/* A calm working shimmer under the transcript while the agent runs
                (Phase F polishes it). Sourced from `agentState`, not
                `thread.isRunning` — see `useGroveChatRuntime`. */}
            {canInterrupt && <WorkingShimmer />}
          </div>

          {/* Jump-to-bottom — auto-disabled (and hidden) when already at bottom;
              sticky so it floats at the viewport's foot. */}
          <div className="pointer-events-none sticky bottom-0 flex justify-center pb-3">
            <ThreadPrimitive.ScrollToBottom asChild>
              <Button
                aria-label="Scroll to bottom"
                variant="outline"
                size="icon-sm"
                className="pointer-events-auto rounded-full disabled:invisible"
              >
                <ArrowDownIcon className="size-4" />
              </Button>
            </ThreadPrimitive.ScrollToBottom>
          </div>
        </ThreadPrimitive.Viewport>

        {notice && (
          // Borderless-quiet: a steering refusal is a passing note, not a boxed
          // alert — it aligns to the chat column and reads as muted meta.
          <p
            data-testid="chat-notice"
            role="status"
            className={cn(CHAT_COLUMN, "py-1 text-xs text-muted-foreground")}
          >
            {notice}
          </p>
        )}

        {/* The agent's current todo/plan list — pinned as a card directly
            above the composer (a sibling of the transcript, not a message), so
            the plan stays visible while you scroll. Column-aligned; absent
            entirely when the session has no todo (degrade to nothing). */}
        {todo && (
          <div className={cn(CHAT_COLUMN, "pb-1")}>
            <TodoListCard todo={todo} />
          </div>
        )}

        {/* Breathing room under the composer: the tone-filled composer floats in
            whitespace; never re-add a separator line here. The bottom pad
            grows to the home-indicator safe-area inset so the composer clears the
            bezel on a notched phone (0 elsewhere); the viewport's
            `interactiveWidget: resizes-content` keeps it above the keyboard. */}
        <div
          className={cn(
            CHAT_COLUMN,
            "pb-[max(0.5rem,env(safe-area-inset-bottom))] sm:pb-[max(0.75rem,env(safe-area-inset-bottom))]",
          )}
        >
          <SteerComposer canInterrupt={canInterrupt} onInterrupt={onInterrupt} />
        </div>
      </div>
    </AssistantRuntimeProvider>
  );
}

/** The steer composer on `ComposerPrimitive`, modern-chat-native: a
 * floating input plane — the textarea over an action row — at the 24px composer
 * radius (`--composer-radius`) on the muted-tinted `--composer-bg`, Enter=send
 * (Shift+Enter newline). The send is a circular terracotta CTA (ArrowUp); the
 * WORKING interrupt is a SEPARATE circular Stop alongside it (never a toggle
 * that hides send — steering a working agent with a follow-up is the product,
 * so the composer never disables while it runs; `adapter.isRunning` stays unset,
 * see `useGroveChatRuntime`). */
function SteerComposer({
  canInterrupt,
  onInterrupt,
}: {
  canInterrupt: boolean;
  onInterrupt: () => void;
}) {
  return (
    <ComposerPrimitive.Root
      data-testid="chat-composer"
      // The one sanctioned shadow in the transcript: a subtle LIGHT-mode lift so
      // the composer reads as a floating input plane (the template's
      // floating-plane exception). `--composer-shadow` is `none` on dark (the
      // surface ladder carries depth) — a theme var, not the `dark:` variant,
      // since Grove flips CSS vars per theme rather than keying the OS media query.
      className="flex w-full flex-col gap-2 rounded-[var(--composer-radius)] border border-border/60 bg-[var(--composer-bg)] p-[var(--composer-padding)] shadow-[var(--composer-shadow)] transition-colors focus-within:border-border"
    >
      <ComposerPrimitive.Input
        submitMode="enter"
        placeholder="Steer the agent…"
        aria-label="Message to the agent"
        // Rests at one row (min-h-10) and auto-grows via `field-sizing-content`
        // up to `max-h-32`; the caret picks up terracotta (`caret-primary`).
        className="field-sizing-content max-h-32 min-h-10 w-full resize-none bg-transparent px-2.5 py-1 text-base text-foreground caret-primary outline-none placeholder:text-muted-foreground/80"
      />
      <div className="flex items-center justify-end gap-1.5">
        {/* Interrupt is offered ONLY while the agent is WORKING — the fast
            emergency path (no menu); interrupting an idle agent is a daemon 409.
            A solid-square (fill-current) circular Stop, error-tinted. */}
        {canInterrupt && (
          <Button
            type="button"
            variant="outline"
            size="icon-xs"
            aria-label="Interrupt agent"
            data-testid="chat-interrupt"
            onClick={onInterrupt}
            className="size-7 rounded-full text-[var(--status-error)] hover:text-[var(--status-error)]"
          >
            <SquareIcon className="size-3.5 fill-current" />
          </Button>
        )}
        <ComposerPrimitive.Send asChild>
          <Button
            type="submit"
            size="icon-xs"
            aria-label="Send message"
            // Circular terracotta CTA (`bg-primary-strong` via the default
            // variant — the AA-safe fill). Quick scale-on-press tactility;
            // transform listed explicitly, dropped under reduced motion.
            className="size-7 rounded-full transition-[transform,color,background-color] active:scale-[0.97] motion-reduce:active:scale-100"
          >
            <ArrowUpIcon />
          </Button>
        </ComposerPrimitive.Send>
      </div>
    </ComposerPrimitive.Root>
  );
}

/** The empty transcript — generic prompt, or a track-a-session CTA when
 * the page hands down a picker (its presence IS the "candidates exist" signal). */
function EmptyState({ picker }: { picker?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 p-6 text-center">
      {/* A quiet brand-neutral icon over the headline (design §5), not italic
          muted prose — the first thing seen on a fresh session. */}
      <MessagesSquare aria-hidden className="size-6 text-muted-foreground/70" />
      {picker ? (
        <>
          <h3 className="text-sm font-medium">No session tracked</h3>
          <p className="text-sm text-muted-foreground">Pick the session Grove should follow.</p>
          <div className="mt-2">{picker}</div>
        </>
      ) : (
        <>
          <h3 className="text-sm font-medium">No conversation yet</h3>
          <p className="text-sm text-muted-foreground">Send a message to steer the agent.</p>
        </>
      )}
    </div>
  );
}

/** A calm "agent working" pulse under the transcript — visual only; the bounded
 * state label is already announced by the header aria-live region, so
 * this stays out of the accessibility tree to avoid a double announce.
 *
 * The dot pulses on the SAME stepped `grove-pulse` cadence as the working
 * state-mark (terminal-cursor blink, not a smooth spinner ease — brand.md §4.1-2),
 * in the working hue so it echoes the glyph rather than adding another accent.
 * Under `prefers-reduced-motion` the dot goes static and the "Working…" label
 * alone carries the signal (the §5 reduced-motion fallback). */
function WorkingShimmer() {
  return (
    <div
      data-testid="chat-shimmer"
      aria-hidden
      className="mt-3 flex items-center gap-2 text-xs text-muted-foreground"
    >
      <span className="inline-block size-1.5 shrink-0 rounded-full bg-[var(--status-active)] animate-grove-pulse motion-reduce:animate-none" />
      <span>Working…</span>
    </div>
  );
}
