"use client";

import { useEffect, useRef, useState } from "react";
import { BellIcon, CheckIcon, ChevronDownIcon, CopyIcon } from "lucide-react";
import {
  MessagePrimitive,
  useMessage,
  type DataMessagePartComponent,
  type TextMessagePartComponent,
} from "@assistant-ui/react";
import { Response } from "@/components/ai-elements/response";
import { ToolGroup } from "@/components/ai-elements/tool";
import { FileEditCard } from "@/components/chat/file-edit-view";
import { QuestionCard } from "@/components/workspace/question-card";
import { RoleLabel } from "@/components/shared/role-label";
import { cn } from "@/lib/utils";
import {
  CHAT_DATA_NAME,
  type ChatItem,
  type FileEditPartData,
  type NotePartData,
  type NotificationPartData,
  type QuestionPartData,
  type ToolsPartData,
} from "@/lib/grove/chat-turns";

/**
 * The single message renderer wired to `ThreadPrimitive.Messages`.
 * One `ChatItem` is one assistant-ui message, so this reads the item's
 * `kind` (stashed in `metadata.custom` by the mapper) and routes:
 *   - the two text kinds (`message` user/assistant) → a bubble carrying the
 *     `chat-message` seam, a native `text` part, the IRC role label, and a
 *     familiar-slot addition (a muted turn timestamp on the user prompt, a
 *     hover/focus copy affordance on the agent reply);
 *   - the historical non-text kinds → their `data-*` part, rendered by the house
 *     components below via `data.by_name` (no `chat-message` seam — a tool run,
 *     note, notification, or question is an event row, not a speech bubble).
 *
 * The LIVE pending question card is NOT a message (it would remount on transcript
 * growth — `ThreadPrimitive.Messages` keys by index); the panel renders it as a
 * sibling after the stream.
 *
 * Rhythm (§4.6, line-free): a top margin per position — 56px before a turn head
 * (a new user prompt or a continuation marker), 24px before an agent reply,
 * 12px before an intra-turn part (tools / note / notification / question). The
 * turn boundary stays ≥2× the inter-message gap so the transcript reads as
 * distinct exchanges without a drawn divider.
 */
export function GroveMessage() {
  const role = useMessage((m) => m.role);
  const index = useMessage((m) => m.index);
  const kind = useMessage((m) => (m.metadata?.custom?.kind as ChatItem["kind"]) ?? "message");
  // Present ONLY on turn heads (the mapper stamps it there) — the timestamp
  // renders exactly where a real per-turn time exists, never on an interjection.
  const startedAt = useMessage((m) => m.metadata?.custom?.startedAt as string | undefined);
  const copyText = useMessage((m) => m.metadata?.custom?.text as string | undefined);

  const spacing = turnSpacing(kind, role, index);

  if (kind === "message" && role === "user") {
    // Right-aligned via the template's grid — a min-72px spacer column keeps the
    // bubble off the left margin without a hard percentage cap.
    return (
      <MessagePrimitive.Root
        data-testid="chat-message"
        data-role="user"
        className={cn("group grid grid-cols-[minmax(72px,1fr)_auto]", spacing)}
      >
        <div className="col-start-2 flex min-w-0 flex-col items-end gap-1.5">
          <RoleLabel role="you" className="-mb-1 self-end" />
          <MessagePrimitive.Parts components={{ Text: UserTextPart }} />
          {startedAt && <TurnTimestamp iso={startedAt} className="self-end" />}
        </div>
      </MessagePrimitive.Root>
    );
  }

  if (kind === "message") {
    // Agent reply — full-width plain prose, no bubble (the Claude
    // pattern). `px-2` is the template's assistant gutter. The copy affordance
    // rides an action bar that RESERVES its own height then pulls it back with a
    // negative margin (the template's -mb-7.5/min-h-7.5 trick), so revealing it
    // on hover never shifts the transcript and the next turn's top margin still
    // measures from the prose, not the (empty-at-rest) action bar.
    return (
      <MessagePrimitive.Root
        data-testid="chat-message"
        data-role="assistant"
        className={cn("group flex w-full flex-col", spacing)}
      >
        <RoleLabel role="agent" className="-mb-1 self-start px-2" />
        <div className="px-2 leading-relaxed text-foreground">
          <MessagePrimitive.Parts components={{ Text: AssistantTextPart }} />
        </div>
        {copyText && (
          // Reserve the action-bar height then pull MOST of it back, so at rest
          // the copy button adds only a small gap (not a full empty row) yet the
          // revealed button on hover stays within the reply's footprint and
          // never overlaps the next part (paired with the 20px intra-turn gap).
          <div className="-mb-5 min-h-7.5 px-2 pt-1.5">
            <CopyButton text={copyText} />
          </div>
        )}
      </MessagePrimitive.Root>
    );
  }

  // Synthetic rows — a single `data-*` part rendered by the house registry.
  return (
    <MessagePrimitive.Root className={cn("w-full min-w-0", spacing)}>
      <MessagePrimitive.Parts components={{ data: { by_name: DATA_RENDERERS } }} />
    </MessagePrimitive.Root>
  );
}

/** Top-margin tier for a message at `index`, keyed on the three-tier §4.6
 * rhythm. The first message never gets one (the viewport already pads). The
 * intra-turn tier is 20px (not 12) so substantial parts — a file-edit diff
 * card especially — get comfortable separation, and so the agent reply's
 * hover copy button (which reveals ~24px below the prose) clears the next
 * part instead of overlapping it (paired with the tamed copy reserve below). */
function turnSpacing(kind: ChatItem["kind"], role: string, index: number): string | undefined {
  if (index === 0) return undefined;
  if (role === "user" || kind === "continuation") return "mt-14"; // 56px turn boundary
  if (kind === "message") return "mt-6"; // 24px inter-message (agent reply)
  return "mt-5"; // 20px intra-turn part
}

// ─── text parts ──────────────────────────────────────────────────────────────

/** Agent prose — streamdown at the transcript's reading measure (`Response`
 *  owns the `text-prose` 16px/1.6 scale + the markdown element map). */
const AssistantTextPart: TextMessagePartComponent = ({ text }) => (
  <div className="min-w-0 max-w-full break-words text-foreground">
    <Response>{text}</Response>
  </div>
);

/** The human prompt, clamped behind the house SmartCollapse (see below). */
const UserTextPart: TextMessagePartComponent = ({ text }) => <CollapsedUserText text={text} />;

// ─── data parts (data.by_name registry) ──────────────────────────────────────

/** A consecutive run of digest tool calls — one collapsed "Used N tools" expander. */
const ToolsPart: DataMessagePartComponent = ({ data }) => (
  <ToolGroup calls={(data as ToolsPartData).calls} data-testid="tool-group" />
);

/** A summary/status digest row — a quiet muted line, no bubble. */
const NotePart: DataMessagePartComponent = ({ data }) => {
  const { tone, text } = data as NotePartData;
  return (
    <p
      data-testid="chat-note"
      data-tone={tone}
      className="text-center text-xs text-muted-foreground"
    >
      {text}
    </p>
  );
};

/** A background-task notice — a quiet labeled row, full result behind a
 * disclosure. */
const NotificationPart: DataMessagePartComponent = ({ data }) => {
  const { summary, detail } = data as NotificationPartData;
  return <NotificationRow summary={summary} detail={detail} />;
};

/** A historical structured question — the read-only choice card. */
const QuestionPart: DataMessagePartComponent = ({ data }) => (
  <div data-testid="chat-question" className="w-full min-w-0">
    <QuestionCard question={(data as QuestionPartData).question} />
  </div>
);

/** A file mutation — an always-visible inline diff, never the tool accordion. */
const FileEditPart: DataMessagePartComponent = ({ data }) => (
  <FileEditCard {...(data as FileEditPartData)} />
);

/** A resumed/compacted session's head — a quiet marker, its turn boundary. */
const ContinuationPart: DataMessagePartComponent = () => (
  <p className="text-center text-xs text-muted-foreground">continued session</p>
);

const DATA_RENDERERS: Record<string, DataMessagePartComponent> = {
  [CHAT_DATA_NAME.tools]: ToolsPart,
  [CHAT_DATA_NAME.note]: NotePart,
  [CHAT_DATA_NAME.notification]: NotificationPart,
  [CHAT_DATA_NAME.question]: QuestionPart,
  [CHAT_DATA_NAME.fileEdit]: FileEditPart,
  [CHAT_DATA_NAME.continuation]: ContinuationPart,
};

// ─── familiar-slot additions (timestamp · copy) ──────────────────────────────

/** A muted per-turn timestamp on the prompt — subtle, tabular, aria-labelled by
 * the full time. `started_at` is per-turn (the wire's only transcript clock), so
 * its home is the turn head. */
function TurnTimestamp({ iso, className }: { iso: string; className?: string }) {
  const date = new Date(iso);
  const label = date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return (
    <time
      dateTime={iso}
      className={cn("select-none text-[11px] tabular-nums text-muted-foreground", className)}
    >
      {label}
    </time>
  );
}

/** Copy the agent reply — reveal on hover/focus (space reserved so hover never
 * shifts layout), reachable by keyboard, aria-labelled. */
function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      aria-label="Copy message"
      onClick={() => {
        void navigator.clipboard?.writeText(text);
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1500);
      }}
      className={cn(
        "inline-flex items-center gap-1 self-start text-xs text-muted-foreground",
        "opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100",
        "hover:text-foreground",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
      )}
    >
      {copied ? (
        <CheckIcon aria-hidden className="size-3.5 text-[var(--ref-add)]" />
      ) : (
        <CopyIcon aria-hidden className="size-3.5" />
      )}
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

// ─── notification row ────────────────────────────────────────────────────────

/** A background-task notice the agent received — summary visible, the subagent's
 * full result behind a disclosure. Moved into the chat module with the Phase-D
 * rebuild (its only consumer). */
function NotificationRow({ summary, detail }: { summary: string; detail: string }) {
  const [open, setOpen] = useState(false);
  const hasDetail = detail.length > 0;

  return (
    <div data-testid="chat-notification" className="w-full min-w-0">
      <button
        type="button"
        aria-expanded={hasDetail ? open : undefined}
        disabled={!hasDetail}
        onClick={() => setOpen((o) => !o)}
        className={cn(
          // Same borderless ghost-trigger grammar as the tool group — a quiet
          // muted one-line disclosure row, no bordered card.
          "flex w-fit max-w-full items-center gap-2 rounded-md py-1.5 text-left text-sm text-muted-foreground",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
          hasDetail && "transition-colors hover:text-foreground",
        )}
      >
        <BellIcon aria-hidden className="size-4 shrink-0" />
        <span className="min-w-0 truncate">{summary}</span>
        {hasDetail && (
          <ChevronDownIcon
            aria-hidden
            className={cn("size-4 shrink-0 transition-transform", !open && "-rotate-90")}
          />
        )}
      </button>
      {hasDetail && open && (
        // The subagent's full result in a soft muted well (the template's
        // detail-well grammar), indented under the trigger — spacing, not a line.
        <p className="ms-6 mt-1 whitespace-pre-wrap break-words rounded-md bg-muted/50 p-2.5 text-xs text-foreground/90">
          {detail}
        </p>
      )}
    </div>
  );
}

// ─── collapsed user prompt (the SmartCollapse pattern) ────────────────────────

/** Lines of a user bubble shown collapsed before the SmartCollapse fade. */
const USER_MESSAGE_CLAMP_LINES = 6;
// text-prose renders at line-height 1.6rem (16px × 1.6); derive the collapsed
// cap from the line count in ONE place (inline style) so a magic Tailwind class
// can't drift from the constant. 6 × 1.6rem = 9.6rem.
const USER_MESSAGE_CLAMP_HEIGHT = `${USER_MESSAGE_CLAMP_LINES * 1.6}rem`;
// Fade the clipped tail instead of a hard cut — softer than truncation, and
// (unlike a char-slice) it never breaks mid-syntax because the full text stays.
const USER_MESSAGE_FADE = "linear-gradient(to bottom, black 60%, transparent 100%)";

/**
 * A user prompt clamped to `USER_MESSAGE_CLAMP_LINES` behind a gradient fade
 * (the Mewbo SmartCollapse pattern) — collapsed by default for EVERY bubble,
 * newest included (product decision). The FULL text is always in the DOM;
 * collapse is purely visual (maxHeight + overflow-hidden + a mask fade), so a
 * long paste can't dominate the transcript yet nothing is ever truncated.
 *
 * Overflow is measured, not guessed: a `ResizeObserver` on the clamped element
 * compares `scrollHeight > clientHeight`, re-running on pane resize (a
 * split-pane drag re-wraps the text and changes what overflows). The toggle
 * shows only when it would do something. In jsdom there is no layout
 * (`scrollHeight` is 0), so nothing overflows and the toggle is naturally
 * absent — the component test pins that no-overflow path; Playwright pins the
 * real clamp. Seams: `user-message-collapse` (carrying `data-collapsed`) and
 * `user-message-toggle`. Owns its bubble tone (the old `MessageContent` wrapper
 * is gone with the ai-elements teardown).
 */
function CollapsedUserText({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  const [overflowing, setOverflowing] = useState(false);
  const clampRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = clampRef.current;
    if (!el) return;
    const measure = () => setOverflowing(el.scrollHeight > el.clientHeight);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [text]);

  const collapsed = !expanded;
  const clamped = collapsed && overflowing;

  return (
    <>
      <div className="ml-auto flex w-fit min-w-0 max-w-full flex-col gap-2 overflow-hidden break-words rounded-xl bg-muted px-4 py-2 text-prose text-foreground">
        <div
          ref={clampRef}
          data-testid="user-message-collapse"
          data-collapsed={collapsed}
          // min-w-0 + break-words so the clamp wrapper never defeats the bubble's
          // wrapping (an unbroken token must still wrap).
          className="min-w-0 break-words"
          style={
            collapsed
              ? {
                  maxHeight: USER_MESSAGE_CLAMP_HEIGHT,
                  overflow: "hidden",
                  // Fade only when text is actually hidden — a short bubble stays
                  // crisp with no gradient over its last line.
                  ...(clamped
                    ? { maskImage: USER_MESSAGE_FADE, WebkitMaskImage: USER_MESSAGE_FADE }
                    : {}),
                }
              : undefined
          }
        >
          {text}
        </div>
      </div>
      {(overflowing || expanded) && (
        <button
          type="button"
          data-testid="user-message-toggle"
          aria-expanded={expanded}
          onClick={() => setExpanded((e) => !e)}
          className={cn(
            "flex items-center gap-1 self-end text-xs text-muted-foreground",
            "transition-colors hover:text-foreground",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
          )}
        >
          {expanded ? "Show less" : "Show more"}
          <ChevronDownIcon
            aria-hidden
            className={cn("size-3.5 shrink-0 transition-transform", expanded && "rotate-180")}
          />
        </button>
      )}
    </>
  );
}
