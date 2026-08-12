"use client";

import { useState } from "react";
import { useAssistantDataUI } from "@assistant-ui/react";

import { CardDisclosure, CardShell } from "@/components/grove/card";
import { GROVE_DATA_NAME } from "@/lib/grove/adapters";
import type {
  ContinuationPartData,
  NotePartData,
  NotificationPartData,
  QuestionPartData,
} from "@/lib/grove/adapters";
import { CodeBlock } from "./code-block";
import { CompactionBoundary } from "./compaction-boundary";
import { FileEditPart } from "./file-edit-part";
import { NestLevel } from "./nesting";
import { HistoricalQuestion } from "./question-view";

/**
 * Renderers for the transcript rows assistant-ui has no native part for.
 *
 * Mounted inside the runtime provider and rendering nothing itself: registration
 * is a side effect of being mounted, so the registry lives beside the thread it
 * serves rather than in a module-level table nobody can see is active.
 *
 * The names must match `GROVE_DATA_NAME` exactly — assistant-ui strips the
 * `data-` prefix when it normalizes a part, so the registry key is the bare
 * name while the content type carries the prefix.
 */
export function GroveDataParts(): null {
  useAssistantDataUI({ name: GROVE_DATA_NAME.note, render: NotePart });
  useAssistantDataUI({ name: GROVE_DATA_NAME.notification, render: NotificationPart });
  useAssistantDataUI({ name: GROVE_DATA_NAME.question, render: QuestionPart });
  useAssistantDataUI({ name: GROVE_DATA_NAME.fileEdit, render: FileEditPart });
  useAssistantDataUI({ name: GROVE_DATA_NAME.continuation, render: ContinuationPart });
  useAssistantDataUI({ name: GROVE_DATA_NAME.compaction, render: CompactionBoundary });
  return null;
}

function NotePart({ data }: { data: NotePartData }) {
  return (
    <p className="text-xs" data-testid="chat-note" data-tone={data.tone}>
      {data.text}
    </p>
  );
}

/**
 * A background task's report — most often a sub-agent's final message. The
 * daemon packs a `<task-notification>` envelope's `<summary>` on the first
 * line and its `<result>` on the rest (`splitNotification`), and for a
 * sub-agent the result IS its last assistant reply: markdown prose, headings,
 * tables and bullets included, not a command's stdout.
 *
 * COLLAPSED BY DEFAULT, composing the same `CardDisclosure` every other
 * collapsible transcript row uses (file edits, the Files tab, the plan card)
 * rather than a bespoke toggle. Collapse hides the DETAIL, never the SIGNAL:
 * the summary line and a line count stay visible either way, the way a
 * file-edit row's path and `+N -N` do.
 *
 * This used to render through the vendored `TerminalBlock` — a fixed-width,
 * "exit 0"-badged fake-terminal card built for a chat-widget's animated
 * command demo, always fully expanded. Neither property fit a markdown
 * report: the monospace terminal face fought the prose it was carrying, and
 * an unbounded, always-open card meant one long report pushed the rest of
 * the transcript below the fold with no way to collapse it back.
 *
 * MARKDOWN, VIA THE SYNTAX HIGHLIGHTER, NOT `MarkdownTextPrimitive`.
 * assistant-ui's markdown renderer takes no text prop — as `titleRuns` in
 * `./selectors` already found for ticket titles, it reads
 * `useMessagePartText()` from assistant-ui's own message context and can
 * only render the message it is mounted inside, so it is structurally
 * inapplicable to a string pulled out of a data part's own payload.
 * `CodeBlock`'s vendored `SyntaxHighlighter` with `language="markdown"` is
 * the honest fallback: real markdown rendering (headings, tables and bullets
 * as ELEMENTS) is unreachable here, but the raw text still reads with syntax
 * colour instead of as one undifferentiated grey block.
 */
function NotificationPart({ data }: { data: NotificationPartData }) {
  const [open, setOpen] = useState(false);
  const lines = data.detail ? data.detail.split("\n") : [];

  if (lines.length === 0) {
    return (
      <p className="text-xs" data-testid="chat-notification">
        {data.summary}
      </p>
    );
  }

  return (
    <CardShell data-testid="chat-notification" data-collapsed={!open}>
      <CardDisclosure
        open={open}
        onOpenChange={setOpen}
        contentClassName="px-3 pb-3"
        summary={
          <>
            <span className="min-w-0 flex-1 truncate text-xs text-content-primary">
              {data.summary}
            </span>
            <span className="shrink-0 text-xs text-content-tertiary tabular-nums">
              {lines.length} {lines.length === 1 ? "line" : "lines"}
            </span>
          </>
        }
      >
        {/* Same mechanism as a tool call's own detail (`./nesting`): the
            report's body is one level deeper than the row that discloses it. */}
        <NestLevel>
          <CodeBlock code={data.detail} language="markdown" />
        </NestLevel>
      </CardDisclosure>
    </CardShell>
  );
}

function QuestionPart({ data }: { data: QuestionPartData }) {
  return <HistoricalQuestion question={data.question} />;
}

/** A resumed or compacted session's head: the turn began with no fresh prompt. */
function ContinuationPart(_props: { data: ContinuationPartData }) {
  return (
    <p className="text-xs" data-testid="chat-continuation">
      Session resumed.
    </p>
  );
}
