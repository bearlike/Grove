"use client";

/**
 * Grove's Thread — a PORT of `components/assistant-ui/thread.tsx`, kept
 * diffable against it line for line.
 *
 * WHY a port and not a composition: the registry's `Thread` sets
 * `--thread-max-width` inline on its root and hard-codes `UserMessage`, and its
 * `components` prop reaches neither. Grove needs a measure that widens outside
 * split view, a user bubble that collapses, and a card that rides with the
 * composer — three things no prop can express. assistant-ui's own `base.tsx`
 * composes `ThreadPrimitive`/`MessagePrimitive` directly, so this is their
 * sanctioned path rather than a bespoke component.
 *
 * THE RULE FOR EDITING THIS FILE: only the deltas below may diverge from the
 * vendored original. Everything else is upstream's typography, spacing and
 * colour, and a "small improvement" here is how the look drifts.
 *
 *   1. `maxWidth` — `--thread-max-width` becomes an input (see `THREAD_WIDTH`),
 *      and the column's flat `px-4` becomes `THREAD_INSET`, which scales with
 *      the thread's own container. Same element, same concern: how wide the
 *      column is and how far it sits from the edge are one decision, and this
 *      is the element that carries BOTH the message stream and the viewport
 *      footer — so the stream, the plan card and the composer cannot drift out
 *      of alignment with each other. Upstream needs no scale because its thread
 *      is never wider than its own centred measure; Grove's is.
 *   2. `footer`   — a caller-supplied node inside `ViewportFooter`, above the
 *                   composer, so it sticks with it instead of stranding at the
 *                   top of the page.
 *   3. `UserMessage` clamps behind a fade (see `./user-message`).
 *   4. `turnAnchor="bottom"` — measured, not preferred. Upstream anchors a
 *      turn's TOP because it streams one message into a conversation you read
 *      forward. Grove loads a COMPLETE historical transcript whose last turn is
 *      a whole agent run, so top-anchoring opened 330px above the tail with the
 *      scroll-to-bottom arrow already showing; `bottom` opens at 0px on every
 *      load. The arrow itself is untouched — same markup, same tooltip, same
 *      `disabled:invisible` — so the mechanism is upstream's and only where it
 *      starts differs.
 *   5. NO MESSAGE ACTION BARS. `AssistantActionBar` (copy / reload / more →
 *      export), `UserActionBar` (edit), both `BranchPicker` mounts and the
 *      `aui_assistant-message-footer` row that carried them are all gone, with
 *      `EditComposer` and the `isEditing` branch that only the edit trigger
 *      could reach. Upstream renders one bar per MESSAGE, which is right for a
 *      chat; Grove maps every transcript ROW — a tool run, a file diff, a
 *      status note, a resume marker — to its own assistant message, so one bar
 *      per message is a bar under every event. Reload and edit are worse than
 *      unused: the daemon exposes no regenerate verb and the external store's
 *      `setMessages` is a deliberate no-op (the daemon owns the transcript), so
 *      both promise an edit that silently goes nowhere. Branch pickers cannot
 *      fire either — a Grove transcript has exactly one branch.
 *   6. The footer's reserved space goes WITH it. Upstream pairs `min-h-7.5
 *      pt-1.5` on the footer with `-mb-7.5 pb-7.5` on the message root — the
 *      padding keeps the bar inside the contained paint box and the negative
 *      margin cancels it in flow. Deleting the bar and keeping the pair leaves
 *      30px of empty gutter under every row, which is the exact complaint.
 *      `relative` stays: it is the positioning context vendored part renderers
 *      inherit, and it costs nothing.
 *   7. Two SCROLL-EDGE SCRIMS plus the sentinels that drive them. Grove's
 *      chrome sits on top of a live transcript — a header above, a plan card
 *      and composer below — and upstream needs no cue because its thread is a
 *      short conversation, not a scrolling log with fixed furniture over it.
 *      Conditional, never decorative: painted only while content is actually
 *      hidden past that edge (see `./scroll-edges`). They are scoped to the
 *      THREAD ROOT, which is what keeps them off the work panel in split view —
 *      that pane is a sibling and cannot be reached from here by construction.
 *
 *   8. The COMPOSER is gated on the runtime's own capability. Upstream mounts
 *      it unconditionally because every thread in the demo is writable; an
 *      archived session's runtime sets `isDisabled`, and upstream's answer to
 *      that is a greyed-out input — which promises an affordance the wire
 *      cannot honour. Keyed off the capability rather than a `composer?:
 *      boolean` prop, so no caller can forget it and read-only stays a runtime
 *      capability rather than becoming a hidden control.
 *   9. The MESSAGE STREAM takes `text-base`. Upstream sets no size here, so the
 *      prose inherited the 16px browser root — the one size in this app nobody
 *      chose. `text-base` is 14px on Grove's ramp, which puts reading prose one
 *      deliberate step above the 13px UI body instead of three accidental ones.
 *      Scoped to the message group rather than the shared column, so it reaches
 *      the stream and not the composer (which already sets its own `text-base`)
 *      or the plan card.
 *
 *      The knock-on is real and accepted: the vendored markdown sets inline
 *      code to `text-[0.85em]`, so it moves 13.6px → 11.9px. It is monospace
 *      with a large x-height, the alternative is editing a vendored file, and
 *      the 12px floor binds Grove code rather than the vendored layer.
 *
 *  10. NO `content-visibility: auto` ON A MESSAGE ROOT. Upstream pairs it with
 *      `contain-intrinsic-size: auto 200px` on both roots, which is right for a
 *      chat of a few dozen even-sized bubbles: offscreen rows cost nothing and
 *      the 200px guess is close enough that nobody feels the correction.
 *
 *      Grove's rows are a tool run, a diff, a paragraph and a status note, so
 *      200px is wrong by an order of magnitude in both directions — and the
 *      moment a row scrolls into view the browser replaces the guess with the
 *      truth. Every correction ABOVE the viewport moves the scroll position
 *      under the reader, and scroll anchoring amplifies rather than absorbs it.
 *      Measured on a 662-row transcript, upward wheel input: 18,000px asked for,
 *      51,199–52,364px travelled (n=3) across 47–49 `scrollHeight` changes
 *      totalling ~37,000px, against a reported height of 144,782px for a
 *      document that is really 53,407px. Downward input was accurate, because
 *      it re-crosses rows already measured — which is exactly the "up is
 *      broken, down is fine" the complaint describes.
 *
 *      Without it the same gesture travels 18,000px exactly, three runs out of
 *      three, with ZERO height changes. It is cheaper everywhere else too, so
 *      there is no trade being made: during the scroll, layouts 227–243 → 13–17
 *      and layout time 720–909ms → 5–9ms; during LOAD, layouts 476 → 40 and
 *      layout time 1,416ms → ~200ms, because `auto` renders its skipped content
 *      against a per-frame budget forever rather than once.
 *
 *      The three mechanisms the complaint pointed at were measured and cleared:
 *      `scroll-behavior: smooth`, the transcript minimap's per-row
 *      `IntersectionObserver` (the minimap itself is since deleted — see git
 *      history for `./turn-scroller`) and the scroll-edge sentinels each left
 *      the overshoot at 52,179 / 55,890 / 55,434px. The cost was never in
 *      Grove's scroll machinery, which is the point to hold onto now that the
 *      minimap is gone: `content-visibility: auto` is unsafe here on its OWN
 *      terms — guess-then-correct sizing over rows whose heights vary by an
 *      order of magnitude, corrected above the viewport, fighting scroll
 *      anchoring — regardless of what else is or is not mounted on the page.
 *      Nothing about deleting the minimap makes it safe to reintroduce.
 *
 *  11. A "Load earlier messages" BUTTON, above `ThreadPrimitive.Messages`,
 *      rendered only while `hasEarlier` is true. Grove's first read is now a
 *      TAIL (`useSessionTurns`'s `INITIAL_TURN_WINDOW`), so a long transcript
 *      has history above the fold that was never fetched — this is the
 *      control that recovers it.
 *
 *      A BUTTON, deliberately, never auto-load-on-scroll-to-top. Delta 10
 *      above is measured evidence that THIS transcript corrupts its own
 *      scroll position when content resizes above the viewport — prepending
 *      a widened window on a scroll event re-creates exactly that trigger,
 *      at the one moment (a reader scrolling up through history) where a
 *      jump would be most noticed. A click is a single, bounded resize the
 *      reader initiated and is already braced for.
 *
 * `components/assistant-ui/thread.tsx` stays in place, unmodified: it is the
 * oracle this file is diffed against. Nothing renders it any more — the
 * archived-session route switched to THIS file precisely so a transcript is
 * rendered one way, which is what delta 9 exists to make safe.
 */

import {
  ComposerAddAttachment,
  ComposerAttachments,
  UserMessageAttachments,
} from "@/components/assistant-ui/attachment";
import { ThreadFollowupSuggestions } from "@/components/assistant-ui/follow-up-suggestions";
import { MarkdownText } from "@/components/assistant-ui/markdown-text";
import {
  Reasoning,
  ReasoningContent,
  ReasoningRoot,
  ReasoningText,
  ReasoningTrigger,
} from "@/components/assistant-ui/reasoning";
import { ToolFallback } from "@/components/assistant-ui/tool-fallback";
import {
  ToolGroupContent,
  ToolGroupRoot,
  ToolGroupTrigger,
} from "@/components/assistant-ui/tool-group";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  AuiIf,
  type AssistantState,
  ComposerPrimitive,
  ErrorPrimitive,
  groupPartByType,
  MessagePrimitive,
  SuggestionPrimitive,
  ThreadPrimitive,
  type ToolCallMessagePartComponent,
  useAuiState,
} from "@assistant-ui/react";
import { ArrowDownIcon, ArrowUpIcon, MicIcon, SquareIcon } from "lucide-react";
import {
  createContext,
  useContext,
  useRef,
  type ComponentType,
  type FC,
  type PropsWithChildren,
  type ReactNode,
} from "react";

import { useScrollEdges } from "./scroll-edges";
import { THREAD_INSET, THREAD_WIDTH } from "./thread-width";
import {
  clampStyle,
  showsToggle,
  useUserMessageCollapse,
} from "./user-message";

export type ThreadGroupPart = MessagePrimitive.GroupedParts.GroupPart;

/**
 * Optional component overrides for the thread. `AssistantMessage` and
 * `Welcome` replace whole sections; the remaining slots override how the
 * assistant message renders tool calls and part groups. Tool UIs registered
 * by name (toolkit `render`, `useAssistantDataUI`) take precedence over
 * `ToolFallback`.
 */
export type ThreadComponents = {
  AssistantMessage?: ComponentType | undefined;
  Welcome?: ComponentType | undefined;
  ToolFallback?: ToolCallMessagePartComponent | undefined;
  ToolGroup?:
    | ComponentType<PropsWithChildren<{ group: ThreadGroupPart }>>
    | undefined;
  ReasoningGroup?:
    | ComponentType<PropsWithChildren<{ group: ThreadGroupPart }>>
    | undefined;
};

export type ThreadProps = {
  components?: ThreadComponents | undefined;
  /** GROVE DELTA 1 — the value for `--thread-max-width`; see {@link THREAD_WIDTH}. */
  maxWidth?: string | undefined;
  /** GROVE DELTA 2 — rendered inside the viewport footer, directly above the
   * composer, so it sticks with the composer rather than scrolling away. */
  footer?: ReactNode;
  /** GROVE DELTA 11 — true when the held window starts above turn 0; shows
   * the "Load earlier messages" button. */
  hasEarlier?: boolean;
  /** GROVE DELTA 11 — true while a `loadEarlier` fetch is in flight. */
  loadingEarlier?: boolean;
  /** GROVE DELTA 11 — widen the held window backwards by one doubling. */
  onLoadEarlier?: () => void;
};

const EMPTY_COMPONENTS: ThreadComponents = {};

const ThreadComponentsContext =
  createContext<ThreadComponents>(EMPTY_COMPONENTS);

// Startup exposes a loading placeholder thread; treat it as a new chat so
// the composer mounts centered. Loads after startup keep the docked layout.
const isNewChatView = (s: AssistantState) =>
  s.thread.messages.length === 0 &&
  (!s.thread.isLoading || s.threads.isLoading);

export const Thread: FC<ThreadProps> = ({
  components = EMPTY_COMPONENTS,
  maxWidth = THREAD_WIDTH.full,
  footer,
  hasEarlier = false,
  loadingEarlier = false,
  onLoadEarlier,
}) => {
  const isEmpty = useAuiState(isNewChatView);

  return (
    <ThreadComponentsContext.Provider value={components}>
      <ThreadRoot
        isEmpty={isEmpty}
        maxWidth={maxWidth}
        footer={footer}
        hasEarlier={hasEarlier}
        loadingEarlier={loadingEarlier}
        onLoadEarlier={onLoadEarlier}
      />
    </ThreadComponentsContext.Provider>
  );
};

const ThreadRoot: FC<{
  isEmpty: boolean;
  maxWidth: string;
  footer: ReactNode;
  hasEarlier: boolean;
  loadingEarlier: boolean;
  onLoadEarlier: (() => void) | undefined;
}> = ({ isEmpty, maxWidth, footer, hasEarlier, loadingEarlier, onLoadEarlier }) => {
  const { Welcome = ThreadWelcome } = useContext(ThreadComponentsContext);
  // GROVE DELTA 7 — the scrims need the viewport ELEMENT as their
  // `IntersectionObserver` root, and upstream neither takes a ref nor renders
  // anything at that level. Held as a ref rather than found by selector so a
  // second thread on the page can never capture the first one's edges.
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const edges = useScrollEdges(viewportRef);

  return (
    <ThreadPrimitive.Root
      className="aui-root aui-thread-root bg-background @container relative flex h-full flex-col"
      style={{
        ["--thread-max-width" as string]: maxWidth,
        ["--composer-bg" as string]:
          "color-mix(in oklab, var(--color-muted) 30%, var(--color-background))",
        ["--composer-radius" as string]: "1.5rem",
        ["--composer-padding" as string]: "8px",
      }}
    >
      {/* GROVE DELTA 7 — the top scroll-edge scrim. It is a child of the ROOT
          and comes BEFORE the viewport on purpose: a positioned element paints
          over the viewport's in-flow messages, while the composer below (also
          positioned, and later in the DOM) still paints over it. That ordering
          is what lets one scrim fade the transcript without ever dimming the
          chrome it exists to make readable. */}
      <div
        aria-hidden
        data-visible={!edges.atTop}
        data-testid="scroll-edge-top"
        className="scroll-edge-top pointer-events-none absolute inset-x-0 top-0 h-8 opacity-0 transition-opacity duration-200 data-[visible=true]:opacity-100"
      />

      <ThreadPrimitive.Viewport
        ref={viewportRef}
        turnAnchor="bottom"
        data-slot="aui_thread-viewport"
        className="relative flex flex-1 flex-col overflow-x-auto overflow-y-scroll scroll-smooth"
      >
        <div
          className={cn(
            "mx-auto flex w-full max-w-(--thread-max-width) flex-1 flex-col pt-4",
            THREAD_INSET,
            isEmpty && "justify-center",
          )}
        >
          {/* GROVE DELTA 7 — zero-height sentinels the edge observer watches.
              They mark the content's own start and end, so the cue stays
              correct as the transcript grows instead of being a measurement
              taken once. */}
          <div ref={edges.topRef} aria-hidden className="h-0 shrink-0" />

          <AuiIf condition={isNewChatView}>
            <Welcome />
          </AuiIf>

          <div
            data-slot="aui_message-group"
            className="mb-14 flex flex-col gap-y-6 text-base empty:hidden"
          >
            {/* GROVE DELTA 11 — a click, not scroll-to-load: see the header
                comment for why this transcript does not auto-load on scroll. */}
            {hasEarlier && (
              <div className="flex justify-center">
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  disabled={loadingEarlier}
                  onClick={onLoadEarlier}
                  data-testid="load-earlier"
                >
                  {loadingEarlier ? "Loading earlier messages…" : "Load earlier messages"}
                </Button>
              </div>
            )}
            <ThreadPrimitive.Messages>
              {() => <ThreadMessage />}
            </ThreadPrimitive.Messages>
          </div>

          <div ref={edges.bottomRef} aria-hidden className="h-0 shrink-0" />

          <ThreadPrimitive.ViewportFooter
            className={cn(
              "aui-thread-viewport-footer bg-background flex flex-col gap-4 overflow-visible pb-4 md:pb-6",
              !isEmpty &&
                "sticky bottom-0 mt-auto rounded-t-(--composer-radius)",
            )}
          >
            {/* GROVE DELTA 7 — the bottom scrim rides the footer rather than
                the root, because the root's bottom edge is BEHIND the composer
                where a scrim would be invisible. `-top-8` puts it immediately
                above the footer's own edge, which is where content actually
                passes out of view. The footer is `sticky` and therefore already
                a containing block, so no extra `relative` is needed. */}
            <div
              aria-hidden
              data-visible={!edges.atBottom}
              data-testid="scroll-edge-bottom"
              className="scroll-edge-bottom pointer-events-none absolute inset-x-0 -top-8 h-8 opacity-0 transition-opacity duration-200 data-[visible=true]:opacity-100"
            />
            <ThreadScrollToBottom />
            <ThreadFollowupSuggestions />
            {footer}
            {/* DELTA 8: the composer is gated on the runtime's own capability.
                Upstream mounts it unconditionally because every thread in the
                demo is writable; an archived session's runtime sets
                `isDisabled`, and upstream's answer to that is a greyed-out
                input — which promises an affordance the wire cannot honour.
                Keyed off the capability rather than a `composer?: boolean`
                prop so no caller can forget it. */}
            <AuiIf condition={(s) => !s.thread.isDisabled}>
              <Composer />
            </AuiIf>
            <AuiIf condition={(s) => isNewChatView(s) && s.composer.isEmpty}>
              <ThreadSuggestions />
            </AuiIf>
          </ThreadPrimitive.ViewportFooter>
        </div>
      </ThreadPrimitive.Viewport>
    </ThreadPrimitive.Root>
  );
};

const ThreadMessage: FC = () => {
  const { AssistantMessage: AssistantMessageComponent = AssistantMessage } =
    useContext(ThreadComponentsContext);
  const role = useAuiState((s) => s.message.role);

  // No `isEditing` branch: DELTA 5 removed the only trigger that could enter
  // message-edit mode, and nothing else in Grove opens a message composer.
  if (role === "user") return <UserMessage />;
  return <AssistantMessageComponent />;
};

const ThreadScrollToBottom: FC = () => {
  return (
    <ThreadPrimitive.ScrollToBottom asChild>
      <TooltipIconButton
        tooltip="Scroll to bottom"
        variant="outline"
        className="aui-thread-scroll-to-bottom dark:border-border dark:bg-background dark:hover:bg-accent absolute -top-12 z-10 self-center rounded-full p-4 disabled:invisible"
      >
        <ArrowDownIcon />
      </TooltipIconButton>
    </ThreadPrimitive.ScrollToBottom>
  );
};

const ThreadWelcome: FC = () => {
  return (
    <div className="aui-thread-welcome-root mb-6 flex flex-col items-center px-4 text-center">
      <h1 className="aui-thread-welcome-message-inner fade-in slide-in-from-bottom-1 animate-in fill-mode-both text-2xl font-semibold duration-200">
        How can I help you today?
      </h1>
    </div>
  );
};

const ThreadSuggestions: FC = () => {
  return (
    <div className="aui-thread-welcome-suggestions flex w-full flex-wrap items-center justify-center gap-2 px-4">
      <ThreadPrimitive.Suggestions>
        {() => <ThreadSuggestionItem />}
      </ThreadPrimitive.Suggestions>
    </div>
  );
};

const ThreadSuggestionItem: FC = () => {
  return (
    <div className="aui-thread-welcome-suggestion-display fade-in slide-in-from-bottom-2 animate-in fill-mode-both duration-200">
      <SuggestionPrimitive.Trigger send asChild>
        <Button
          variant="ghost"
          className="aui-thread-welcome-suggestion text-foreground hover:bg-muted border-border/60 h-auto gap-1.5 rounded-full border px-3.5 py-1.5 text-sm font-normal whitespace-nowrap transition-colors"
        >
          <SuggestionPrimitive.Title className="aui-thread-welcome-suggestion-text-1" />
          <SuggestionPrimitive.Description className="aui-thread-welcome-suggestion-text-2 empty:hidden" />
        </Button>
      </SuggestionPrimitive.Trigger>
    </div>
  );
};

const Composer: FC = () => {
  return (
    <ComposerPrimitive.Root className="aui-composer-root relative flex w-full flex-col">
      <ComposerPrimitive.AttachmentDropzone asChild>
        <div
          data-slot="aui_composer-shell"
          className="border-border/60 data-[dragging=true]:border-ring focus-within:border-border dark:border-muted-foreground/15 dark:focus-within:border-muted-foreground/30 flex w-full flex-col gap-2 rounded-(--composer-radius) border bg-(--composer-bg) p-(--composer-padding) shadow-[0_4px_16px_-8px_rgba(0,0,0,0.08),0_1px_2px_rgba(0,0,0,0.04)] transition-[border-color,box-shadow] focus-within:shadow-[0_6px_24px_-8px_rgba(0,0,0,0.12),0_1px_2px_rgba(0,0,0,0.05)] data-[dragging=true]:border-dashed data-[dragging=true]:bg-[color-mix(in_oklab,var(--color-accent)_50%,var(--color-background))] dark:shadow-none"
        >
          <ComposerAttachments />
          <ComposerPrimitive.Input
            placeholder="Send a message..."
            className="aui-composer-input caret-primary placeholder:text-muted-foreground/80 max-h-32 min-h-10 w-full resize-none bg-transparent px-2.5 py-1 text-base outline-none"
            rows={1}
            autoFocus
            enterKeyHint="send"
            aria-label="Message input"
          />
          <ComposerAction />
        </div>
      </ComposerPrimitive.AttachmentDropzone>
    </ComposerPrimitive.Root>
  );
};

const ComposerAction: FC = () => {
  return (
    <div className="aui-composer-action-wrapper relative flex items-center justify-between">
      <ComposerAddAttachment />
      <div className="flex items-center gap-1.5">
        <AuiIf condition={(s) => s.thread.capabilities.dictation}>
          <AuiIf condition={(s) => s.composer.dictation == null}>
            <ComposerPrimitive.Dictate asChild>
              <TooltipIconButton
                tooltip="Voice input"
                side="bottom"
                type="button"
                variant="ghost"
                size="icon"
                className="aui-composer-dictate size-7 rounded-full"
                aria-label="Start voice input"
              >
                <MicIcon className="aui-composer-dictate-icon size-4" />
              </TooltipIconButton>
            </ComposerPrimitive.Dictate>
          </AuiIf>
          <AuiIf condition={(s) => s.composer.dictation != null}>
            <ComposerPrimitive.StopDictation asChild>
              <TooltipIconButton
                tooltip="Stop dictation"
                side="bottom"
                type="button"
                variant="ghost"
                size="icon"
                className="aui-composer-stop-dictation text-destructive size-7 rounded-full"
                aria-label="Stop voice input"
              >
                <SquareIcon className="aui-composer-stop-dictation-icon size-3.5 animate-pulse fill-current" />
              </TooltipIconButton>
            </ComposerPrimitive.StopDictation>
          </AuiIf>
        </AuiIf>
        <AuiIf condition={(s) => !s.thread.isRunning}>
          <ComposerPrimitive.Send asChild>
            <TooltipIconButton
              tooltip="Send message"
              side="bottom"
              type="button"
              variant="default"
              size="icon"
              className="aui-composer-send size-7 rounded-full"
              aria-label="Send message"
            >
              <ArrowUpIcon className="aui-composer-send-icon size-4.5" />
            </TooltipIconButton>
          </ComposerPrimitive.Send>
        </AuiIf>
        <AuiIf condition={(s) => s.thread.isRunning}>
          <ComposerPrimitive.Cancel asChild>
            <Button
              type="button"
              variant="default"
              size="icon"
              className="aui-composer-cancel size-7 rounded-full"
              aria-label="Stop generating"
            >
              <SquareIcon className="aui-composer-cancel-icon size-3.5 fill-current" />
            </Button>
          </ComposerPrimitive.Cancel>
        </AuiIf>
      </div>
    </div>
  );
};

const MessageError: FC = () => {
  return (
    <MessagePrimitive.Error>
      <ErrorPrimitive.Root className="aui-message-error-root border-destructive bg-destructive/10 text-destructive dark:bg-destructive/5 mt-2 rounded-md border p-3 text-sm dark:text-red-200">
        <ErrorPrimitive.Message className="aui-message-error-message line-clamp-2" />
      </ErrorPrimitive.Root>
    </MessagePrimitive.Error>
  );
};

const AssistantMessage: FC = () => {
  const {
    ToolFallback: ToolFallbackComponent = ToolFallback,
    ToolGroup,
    ReasoningGroup,
  } = useContext(ThreadComponentsContext);

  return (
    <MessagePrimitive.Root
      data-slot="aui_assistant-message-root"
      data-role="assistant"
      className="fade-in slide-in-from-bottom-1 animate-in relative duration-150"
    >
      <div
        data-slot="aui_assistant-message-content"
        className="text-foreground px-2 leading-relaxed wrap-break-word"
      >
        <MessagePrimitive.GroupedParts
          groupBy={groupPartByType({
            reasoning: ["group-chainOfThought", "group-reasoning"],
            "tool-call": ["group-chainOfThought", "group-tool"],
            "standalone-tool-call": [],
          })}
        >
          {({ part, children }) => {
            switch (part.type) {
              case "group-chainOfThought":
                return <div data-slot="aui_chain-of-thought">{children}</div>;
              case "group-tool":
                if (ToolGroup) {
                  return <ToolGroup group={part}>{children}</ToolGroup>;
                }
                return (
                  <ToolGroupRoot variant="ghost">
                    <ToolGroupTrigger
                      count={part.indices.length}
                      active={part.status.type === "running"}
                    />
                    <ToolGroupContent>{children}</ToolGroupContent>
                  </ToolGroupRoot>
                );
              case "group-reasoning": {
                if (ReasoningGroup) {
                  return (
                    <ReasoningGroup group={part}>{children}</ReasoningGroup>
                  );
                }
                const running = part.status.type === "running";
                return (
                  <ReasoningRoot streaming={running}>
                    <ReasoningTrigger active={running} />
                    <ReasoningContent aria-busy={running}>
                      <ReasoningText>{children}</ReasoningText>
                    </ReasoningContent>
                  </ReasoningRoot>
                );
              }
              case "text":
                return <MarkdownText />;
              case "reasoning":
                return <Reasoning {...part} />;
              case "tool-call":
                return part.toolUI ?? <ToolFallbackComponent {...part} />;
              case "data":
                return part.dataRendererUI;
              case "indicator":
                return (
                  <span
                    data-slot="aui_assistant-message-indicator"
                    className="animate-pulse font-sans"
                    aria-label="Assistant is working"
                  >
                    {"●"}
                  </span>
                );
              default:
                return null;
            }
          }}
        </MessagePrimitive.GroupedParts>
        <MessageError />
      </div>
    </MessagePrimitive.Root>
  );
};

/**
 * GROVE DELTA 3 — the user bubble clamps behind a fade.
 *
 * A Grove prompt is routinely a whole spec, and at full height a handful of
 * them push every reply off the screen. The clamp is purely visual — max
 * height, hidden overflow and a mask — so the full text is always in the DOM
 * and a copy or a find-in-page still sees it; a character slice would cut mid
 * syntax. Every bubble starts collapsed, newest included.
 *
 * The clamp rides the bubble itself rather than an inner wrapper because
 * `empty:hidden` stops matching the moment a wrapper element exists, which
 * would leave an empty bubble drawing a filled rectangle. Upstream's paired
 * `peer` class stays on the bubble even though DELTA 5 removed its only
 * consumer (`peer-empty:hidden` on the edit affordance): it is inert, and
 * keeping it is what keeps this block diffable against the oracle.
 */
const UserMessage: FC = () => {
  const collapse = useUserMessageCollapse();
  const collapsed = !collapse.expanded;

  return (
    <MessagePrimitive.Root
      data-slot="aui_user-message-root"
      className="fade-in slide-in-from-bottom-1 animate-in grid auto-rows-auto grid-cols-[minmax(72px,1fr)_auto] content-start gap-y-2 px-2 duration-150 [&:where(>*)]:col-start-2"
      data-role="user"
    >
      <UserMessageAttachments />

      <div className="aui-user-message-content-wrapper relative col-start-2 flex min-w-0 flex-col items-end">
        <div
          ref={collapse.ref}
          data-testid="user-message-collapse"
          data-collapsed={collapsed}
          className="aui-user-message-content peer bg-muted text-foreground w-full rounded-xl px-4 py-2 wrap-break-word empty:hidden"
          style={clampStyle(collapsed, collapse.overflowing)}
        >
          <MessagePrimitive.Parts />
        </div>
        {showsToggle(collapse.overflowing, collapse.expanded) && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            data-testid="user-message-toggle"
            aria-expanded={collapse.expanded}
            onClick={collapse.toggle}
            className="text-muted-foreground mt-1 h-6 gap-1 px-2 text-xs font-normal"
          >
            {collapse.expanded ? "Show less" : "Show more"}
            <ArrowDownIcon
              aria-hidden
              className={cn("size-3.5", collapse.expanded && "rotate-180")}
            />
          </Button>
        )}
      </div>
    </MessagePrimitive.Root>
  );
};
