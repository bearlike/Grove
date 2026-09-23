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
 *   1. `maxWidth`/`inset` — `--thread-max-width` and the column's flat `px-4`
 *      both become inputs (see `THREAD_WIDTH` / `THREAD_INSET`), keyed by the
 *      same mode. Same element, same concern: how wide the
 *      column is and how far it sits from the edge are one decision, and this
 *      is the element that carries BOTH the message stream and the viewport
 *      footer — so the stream, the plan card and the composer cannot drift out
 *      of alignment with each other. Upstream needs no scale because its thread
 *      is never wider than its own centred measure; Grove's is.
 *   2. `footer`   — a caller-supplied node in the fixed thread footer, above
 *                   the composer. It is a flex sibling of the viewport: a
 *                   sticky descendant overlaps transcript rows by definition.
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
 *   7. Two persistent edge shadows give the fixed header and composer depth.
 *      The positioned, clipping thread root contains their paint so neither
 *      shadow reaches the work panel. The lower edge ends at the composer and
 *      fades upward behind the status region at the pane's width.
 *
 *  15. The footer is a FLEX SIBLING of the viewport. A sticky footer stays in
 *      the scroller's paint box and therefore overlaps its last rows; putting
 *      the whole footer outside makes the viewport's own flex height end above
 *      every card and the composer without measured-height arithmetic. It is a
 *      plain div: `ViewportFooter` consumes the viewport context and must stay
 *      inside the viewport; the scroll control remains there for the same reason.
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
 *  12. ATTACHMENTS RENDER AS `File` ROWS, in the composer and on a sent
 *      message, through ONE Grove composition (`grove/attachment-file`).
 *      Upstream mounts the vendored `ComposerAttachments` tile grid and
 *      `UserMessageAttachments`, both drawing the attachment TILE — one
 *      hard-coded `FileText` glyph for every non-image file, the name hidden
 *      in a tooltip — and neither takes a prop that would change either. The
 *      vendored `File` element is what assistant-ui's own page names for a
 *      file on a message, so both mounts become `./composer-attachment`'s rows:
 *      the same primitives (`ComposerPrimitive.Attachments`,
 *      `MessagePrimitive.Attachments`, `AttachmentPrimitive.Root`) composed
 *      around `File.Root/Icon/Name/Size`.
 *
 *      The sent message's files ride `message.attachments` — lifted off the
 *      daemon's text by `adapters/attachments` — and NOT `file` content parts,
 *      because the grid's first row is above the bubble and outside DELTA 3's
 *      clamp, while a content part is the first thing the clamp hides.
 *
 *  13. RESPONSE CONTENT OPENS ITS LINKS IN A NEW TAB. The assistant message's
 *      content element is `NewTabLinks` rather than a plain `div`, so every
 *      anchor the answer renders — Markdown, reasoning, tool cards, data
 *      parts — carries `target="_blank"` and `rel="noopener noreferrer"`.
 *      A Grove workspace page is a streaming transcript, a scroll position and
 *      an attached terminal, and a citation that replaces all of it is a loss
 *      rather than a navigation. It sits HERE, on the region, because the
 *      vendored Markdown renderer hard-codes its own `components` map and
 *      exposes no `a` — and because fixing the anchor per renderer would still
 *      miss every card. See `./new-tab-links` for the whole argument.
 *
 *  14. THE COMPOSER IS THE CALLER'S. Upstream mounts its own `Composer`; the
 *      workspace passes `workspace/composer`'s surface through the `composer`
 *      prop, and every other caller is read-only, so none is mounted there.
 *
 * `components/assistant-ui/thread.tsx` stays in place, unmodified: it is the
 * oracle this file is diffed against. Nothing renders it any more — the
 * archived-session route switched to THIS file precisely so a transcript is
 * rendered one way, which is what delta 9 exists to make safe.
 */

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
  ErrorPrimitive,
  groupPartByType,
  MessagePrimitive,
  SuggestionPrimitive,
  ThreadPrimitive,
  type ToolCallMessagePartComponent,
  useAuiState,
} from "@assistant-ui/react";
import { ArrowDownIcon } from "lucide-react";
import {
  createContext,
  useContext,
  type ComponentType,
  type FC,
  type PropsWithChildren,
  type ReactNode,
} from "react";

import { MessageAttachmentRows } from "./composer-attachment";
import { NewTabLinks } from "./new-tab-links";
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
    ComponentType<PropsWithChildren<{ group: ThreadGroupPart }>> | undefined;
  ReasoningGroup?:
    ComponentType<PropsWithChildren<{ group: ThreadGroupPart }>> | undefined;
};

export type ThreadProps = {
  components?: ThreadComponents | undefined;
  /** GROVE DELTA 1 — the value for `--thread-max-width`; see {@link THREAD_WIDTH}. */
  maxWidth?: string | undefined;
  /** GROVE DELTA 1 — the column's horizontal margin, keyed by the same mode as
   * `maxWidth`; see {@link THREAD_INSET}. */
  inset?: string | undefined;
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
  /** GROVE DELTA 14 — the workspace composer belongs to the workspace seam,
   * not this reusable transcript port. */
  composer?: ReactNode;
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
  inset = THREAD_INSET.full,
  footer,
  hasEarlier = false,
  loadingEarlier = false,
  onLoadEarlier,
  composer,
}) => {
  const isEmpty = useAuiState(isNewChatView);

  return (
    <ThreadComponentsContext.Provider value={components}>
      <ThreadRoot
        isEmpty={isEmpty}
        maxWidth={maxWidth}
        inset={inset}
        footer={footer}
        hasEarlier={hasEarlier}
        loadingEarlier={loadingEarlier}
        onLoadEarlier={onLoadEarlier}
        composer={composer}
      />
    </ThreadComponentsContext.Provider>
  );
};

const ThreadRoot: FC<{
  isEmpty: boolean;
  maxWidth: string;
  inset: string;
  footer: ReactNode;
  hasEarlier: boolean;
  loadingEarlier: boolean;
  onLoadEarlier: (() => void) | undefined;
  composer: ReactNode;
}> = ({
  isEmpty,
  maxWidth,
  inset,
  footer,
  hasEarlier,
  loadingEarlier,
  onLoadEarlier,
  composer,
}) => {
  const { Welcome = ThreadWelcome } = useContext(ThreadComponentsContext);

  return (
    <ThreadPrimitive.Root
      className="aui-root aui-thread-root bg-background @container relative isolate flex h-full flex-col overflow-hidden"
      style={{
        ["--thread-max-width" as string]: maxWidth,
      }}
    >
      {/* GROVE DELTA 7 — chrome depth stays inside this pane. Explicit stacking
          keeps the top shadow above the positioned viewport, not behind it. */}
      <div
        aria-hidden
        data-testid="scroll-edge-top"
        className="scroll-edge-top pointer-events-none absolute inset-x-0 top-0 z-10 h-6"
      />

      <ThreadPrimitive.Viewport
        turnAnchor="bottom"
        data-slot="aui_thread-viewport"
        className="relative min-h-0 flex-1 overflow-x-auto overflow-y-scroll scroll-smooth"
      >
        <div
          className={cn(
            "mx-auto flex w-full max-w-(--thread-max-width) flex-col pt-6",
            inset,
            isEmpty && "min-h-full justify-center",
          )}
        >
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
                  {loadingEarlier
                    ? "Loading earlier messages…"
                    : "Load earlier messages"}
                </Button>
              </div>
            )}
            <ThreadPrimitive.Messages>
              {() => <ThreadMessage />}
            </ThreadPrimitive.Messages>
          </div>
        </div>
        <div className="sticky bottom-4 z-10 flex h-0 justify-center">
          <ThreadScrollToBottom />
        </div>
      </ThreadPrimitive.Viewport>

      <div
        data-testid="transcript-footer"
        className="aui-thread-viewport-footer bg-surface-base relative z-10 max-h-[65%] shrink-0 overflow-y-auto"
      >
        <div
          className={cn(
            "mx-auto flex w-full max-w-(--thread-max-width) flex-col pb-4 md:pb-6",
            inset,
          )}
        >
          {/* The fade ends at the composer, not above the generating status.
              Let this region size it so loader/queue changes need no measurement. */}
          <div className="relative isolate flex flex-col gap-4 pb-4">
            <AuiIf condition={(s) => !s.thread.isDisabled}>
              <div
                aria-hidden
                data-testid="scroll-edge-bottom"
                className="scroll-edge-bottom pointer-events-none absolute left-1/2 -top-6 bottom-0 -z-10 w-[100cqw] -translate-x-1/2"
              />
            </AuiIf>
            <ThreadFollowupSuggestions />
            {footer}
          </div>
          {/* DELTA 8: the composer is gated on the runtime's own capability.
              Upstream mounts it unconditionally because every thread in the
              demo is writable; an archived session's runtime sets
              `isDisabled`, and upstream's answer to that is a greyed-out
              input — which promises an affordance the wire cannot honour.
              Keyed off the capability rather than a `composer?: boolean`
              prop so no caller can forget it. */}
          <AuiIf condition={(s) => !s.thread.isDisabled}>
            {composer}
          </AuiIf>
          <AuiIf condition={(s) => isNewChatView(s) && s.composer.isEmpty}>
            <ThreadSuggestions />
          </AuiIf>
        </div>
      </div>
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
        className="aui-thread-scroll-to-bottom dark:border-border dark:bg-background dark:hover:bg-accent -translate-y-full rounded-full p-4 disabled:invisible"
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
      {/* GROVE DELTA 13: response content owns its link targets. Everything below is
          the agent's answer — Markdown, reasoning, tool cards, data parts — and
          a link in it must not replace the session the reader is in. See
          `new-tab-links.tsx` for why this is a region rather than an anchor. */}
      <NewTabLinks
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
      </NewTabLinks>
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
      {/* GROVE DELTA 12 — the message's files, as `File` rows in the grid's
          first row: ABOVE the bubble and outside its clamp, so every attached
          file stays visible while the prompt is collapsed. */}
      <MessageAttachmentRows />

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
