"use client";

import { useAuiState } from "@assistant-ui/react";
import { BanIcon } from "lucide-react";

import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { ExpandedComposer } from "@/components/grove/composer";
import { useInterrupt, useWorkspacePeek } from "@/lib/grove/hooks";
import { canInterruptNative } from "./selectors";
import { WorkspaceComposer } from "./thread";
import { ComposerModel } from "./composer-model";

/**
 * The workspace reply composer, in its inline seat or the shared expand dialog.
 *
 * `ExpandedComposer` owns the flag, the dialog and the focus restore for both
 * Grove composers, so this file says only what is workspace-specific: which
 * controls ride the toolbar, and what the dialog is called. The composer is
 * MOVED rather than cloned — one draft, one editor, one accessible name — and
 * that is affordable here because the draft and every attachment already live
 * in the assistant-ui runtime above this component, so the remount carries
 * nothing.
 */
export function NativeInterrupt({ workspaceId }: { readonly workspaceId: string }): React.ReactNode {
  const { data } = useWorkspacePeek(workspaceId);
  const interrupt = useInterrupt(workspaceId);
  const disabled = useAuiState((state) => state.thread.isDisabled);
  if (!data?.state.native) return null;
  return (
    <TooltipIconButton
      type="button"
      size="icon"
      variant="ghost"
      tooltip={interrupt.error?.message ?? (interrupt.isSuccess ? "Interrupt request delivered" : "Interrupt agent")}
      aria-label="Interrupt agent"
      disabled={disabled || !canInterruptNative(data.state) || interrupt.isPending}
      onClick={() => interrupt.mutate()}
      data-testid="composer-native-interrupt"
    >
      <BanIcon aria-hidden />
    </TooltipIconButton>
  );
}

export function WorkspaceComposerSurface({
  workspaceId,
  exitReason,
}: {
  readonly workspaceId: string;
  /** The native owner ended; the next send resumes this conversation. */
  readonly exitReason: string | null;
}): React.ReactNode {
  return (
    <ExpandedComposer
      // NOT "Message input": that is the textarea's own accessible name, and
      // Radix labels the dialog from this title — reusing it gives one screen
      // reader two different things called the same thing, nested.
      title="Expand message"
      description="Write with more room without leaving this session."
      testId="workspace-composer"
      expandLabel="Expand composer"
    >
      {(expanded, inputRef, expandControl) => (
        <WorkspaceComposer
          // The inline editor is the focus-restore target, so only the inline
          // mount claims the ref: handing it to the dialog's copy too would
          // leave it pointing at whichever unmounted last.
          inputRef={expanded ? undefined : inputRef}
          toolbar={
            <>
              <ComposerModel workspaceId={workspaceId} />
              {expandControl}
              <NativeInterrupt workspaceId={workspaceId} />
            </>
          }
          notice={
            exitReason ? "This session ended; sending a message will restart it and continue the conversation." : null
          }
          expanded={expanded}
          draftKey={workspaceId}
        />
      )}
    </ExpandedComposer>
  );
}
