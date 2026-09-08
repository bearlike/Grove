"use client";

import { useAssistantDataUI } from "@assistant-ui/react";

import { GROVE_DATA_NAME } from "@/lib/grove/adapters";
import type {
  ContinuationPartData,
  NotePartData,
  NotificationPartData,
  QuestionPartData,
} from "@/lib/grove/adapters";
import { AgentMessage } from "./agent-message";
import { CompactionBoundary } from "./compaction-boundary";
import { FileEditPart } from "./file-edit-part";
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
  useAssistantDataUI({ name: GROVE_DATA_NAME.mailbox, render: MailboxPart });
  useAssistantDataUI({ name: GROVE_DATA_NAME.question, render: QuestionPart });
  useAssistantDataUI({ name: GROVE_DATA_NAME.fileEdit, render: FileEditPart });
  useAssistantDataUI({ name: GROVE_DATA_NAME.continuation, render: ContinuationPart });
  useAssistantDataUI({ name: GROVE_DATA_NAME.compaction, render: CompactionBoundary });
  return null;
}

function MailboxPart({ data }: { data: import("@/lib/grove/adapters/agent-message").AgentMessageData }) {
  return <AgentMessage message={data} />;
}

function NotePart({ data }: { data: NotePartData }) {
  return (
    <p className="text-xs" data-testid="chat-note" data-tone={data.tone}>
      {data.text}
    </p>
  );
}

/** Task completion notices use the same readable disclosure even without a result. */
function NotificationPart({ data }: { data: NotificationPartData }) {
  return <AgentMessage message={{ from: null, to: "This session", subject: data.summary, body: data.detail || data.summary }} />;
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
