"use client";

import { AssistantRuntimeProvider } from "@assistant-ui/react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ErrorState } from "@/components/elements/error-state";
import { GroveDataParts } from "./data-parts";
import { Thread, type ThreadComponents } from "./thread";
import { TranscriptSkeleton } from "./transcript-skeleton";
import { GROVE_THREAD_COMPONENTS } from "./tool-call-part";
import { useSessionTurns } from "@/lib/grove/hooks";
import { useReadOnlyTranscript } from "@/lib/grove/runtime";

const SUPPRESSED_WELCOME_COMPONENTS: ThreadComponents = {
  ...GROVE_THREAD_COMPONENTS,
  Welcome: () => null,
};

/**
 * A child session's transcript, read through the SAME renderer the parent
 * uses — never a second component — and never able to steer or remap the
 * parent's session or composer draft.
 */
export function SubagentTranscriptDialog({
  workspaceId,
  sessionId,
  label,
  onOpenChange,
}: {
  workspaceId: string;
  /** Null closes the dialog; a real id opens it against that child's turns. */
  sessionId: string | null;
  label: string | null;
  onOpenChange: (open: boolean) => void;
}): React.ReactNode {
  return (
    <Dialog open={sessionId !== null} onOpenChange={onOpenChange}>
      <DialogContent
        className="flex max-h-[90dvh] w-[calc(100vw-2rem)] flex-col gap-4 sm:max-w-3xl"
        data-testid="subagent-transcript-dialog"
      >
        <DialogHeader>
          <DialogTitle>{label ? `${label} — subagent transcript` : "Subagent transcript"}</DialogTitle>
          <DialogDescription>Read-only. Sending stays with the parent session below.</DialogDescription>
        </DialogHeader>
        {sessionId && <SubagentTranscriptBody workspaceId={workspaceId} sessionId={sessionId} />}
      </DialogContent>
    </Dialog>
  );
}

function SubagentTranscriptBody({
  workspaceId,
  sessionId,
}: {
  workspaceId: string;
  sessionId: string;
}): React.ReactNode {
  const turns = useSessionTurns(workspaceId, sessionId).query;
  const { runtime, isEmpty } = useReadOnlyTranscript(turns.data?.turns);

  if (turns.isPending) return <TranscriptSkeleton />;
  if (turns.isError) {
    return (
      <ErrorState
        className="m-4"
        title="Couldn’t load this subagent’s transcript"
        detail={turns.error.message}
        retrying={turns.isFetching}
        onRetry={() => void turns.refetch()}
      />
    );
  }

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <GroveDataParts />
      <div className="flex min-h-0 min-w-0 flex-1 flex-col" data-testid="subagent-transcript">
        <Thread components={isEmpty ? GROVE_THREAD_COMPONENTS : SUPPRESSED_WELCOME_COMPONENTS} />
      </div>
    </AssistantRuntimeProvider>
  );
}
