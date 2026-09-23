"use client";

import {
  AuiIf,
  ComposerPrimitive,
  unstable_useMentionAdapter,
  unstable_useSlashCommandAdapter,
  useAui,
  useAuiState,
} from "@assistant-ui/react";
import { AtSignIcon, BanIcon, MicIcon, SquareIcon, SquareSlashIcon } from "lucide-react";
import { useCallback, useEffect, useMemo, useState, type ReactNode, type RefObject } from "react";
import { toast } from "sonner";

import { ComposerTriggerPopover } from "@/components/assistant-ui/composer-trigger-popover";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import {
  Composer,
  ComposerActions,
  ComposerAttachButton,
  ComposerBar,
  ComposerSend,
  ComposerToolbar,
} from "@/components/elements/composer";
import { ContextDisplay } from "@/components/elements/context-display";
import { ExpandedComposer } from "@/components/grove/composer";
import { useWorkspaceOnboardingDemands } from "@/components/grove/onboarding";
import { COMPOSER_COMMANDS, mentionsFromContacts } from "@/lib/grove/adapters";
import {
  useInterrupt,
  useInvokeControl,
  useMailboxContacts,
  useWorkspaceActivity,
  useWorkspacePeek,
} from "@/lib/grove/hooks";
import { cn } from "@/lib/utils";
import { ComposerAttachmentRows } from "./composer-attachment";
import { ComposerModel } from "./composer-model";
import { canInterruptNative } from "./selectors";
import { flushComposerDraft, useComposerDraft } from "./use-composer-draft";

/** The tour's one ask of the composer. A module constant so the hook's deps stay stable. */
const TOUR_KINDS = ["workspace-prompt"] as const;

/** `iconMap` keys for the trigger popover; each adapter item names one. */
const TRIGGER_ICONS = { command: SquareSlashIcon, agent: AtSignIcon } as const;

/**
 * The workspace reply composer, in its inline seat or the shared expand dialog.
 *
 * ONE SURFACE, assistant-ui's own: the vendored `elements/composer` anatomy
 * (`Composer` → `ComposerBar` → attachments, input, `ComposerToolbar`) driven by
 * `ComposerPrimitive`, with the reference's two character triggers — `/` for
 * the commands Grove can deliver, `@` for the live peers an agent can write to.
 * The toolbar reads left to right as "add to the message" (attach) against
 * "how it is sent" (context, model, expand, interrupt, send).
 *
 * The composer is MOVED rather than cloned by `ExpandedComposer`; that is
 * affordable because the draft and every attachment live in the assistant-ui
 * runtime above this component, so the remount carries nothing.
 */
export function WorkspaceComposerSurface({
  workspaceId,
  exitReason,
}: {
  readonly workspaceId: string;
  /** The native owner ended; the next send resumes this conversation. */
  readonly exitReason: string | null;
}): ReactNode {
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
          workspaceId={workspaceId}
          // The inline editor is the focus-restore target, so only the inline
          // mount claims the ref: handing it to the dialog's copy too would
          // leave it pointing at whichever unmounted last.
          inputRef={expanded ? undefined : inputRef}
          expanded={expanded}
          expandControl={expandControl}
          notice={
            exitReason
              ? "This session ended; sending a message will restart it and continue the conversation."
              : null
          }
        />
      )}
    </ExpandedComposer>
  );
}

function WorkspaceComposer({
  workspaceId,
  inputRef,
  expanded,
  expandControl,
  notice,
}: {
  readonly workspaceId: string;
  readonly inputRef: RefObject<HTMLTextAreaElement | null> | undefined;
  /** The dialog moves this same composer and lets its editor use the available height. */
  readonly expanded: boolean;
  readonly expandControl: ReactNode;
  /** A session-state explanation immediately above the next send action. */
  readonly notice: string | null;
}): ReactNode {
  const outstanding = useDraftRestore(workspaceId, expanded);
  const slash = useComposerCommands(workspaceId);
  const mention = useComposerMentions(workspaceId);

  return (
    // The trigger root wraps the whole bar, not just the input: both popovers
    // are siblings of `ComposerPrimitive.Root`, anchored to the bar's top edge.
    <ComposerPrimitive.Unstable_TriggerPopoverRoot>
      <Composer className={cn("max-w-none", expanded && "flex min-h-0 flex-1 flex-col")}>
        <ComposerTriggerPopover char="/" iconMap={TRIGGER_ICONS} {...slash} />
        <ComposerTriggerPopover char="@" iconMap={TRIGGER_ICONS} {...mention} />
        <ComposerPrimitive.Root className={cn("flex w-full flex-col", expanded && "min-h-0 flex-1")}>
          {/* `asChild` ONTO the bar, never a wrapper around it: the dropzone sets
              `data-dragging` on whatever element it renders, and the bar is the
              element the theme shows that on. */}
          <ComposerPrimitive.AttachmentDropzone asChild>
            <ComposerBar className={cn(expanded && "min-h-0 flex-1")}>
              <ComposerAttachmentRows />
              {outstanding.length > 0 && (
                <p role="status" className="px-3 text-xs text-content-tertiary" data-testid="composer-pending-re-add">
                  Re-add to send: <span className="text-content-secondary">{outstanding.join(", ")}</span>
                </p>
              )}
              {notice ? (
                <p role="status" className="px-3 text-xs text-content-secondary" data-testid="composer-session-ended">
                  {notice}
                </p>
              ) : null}
              <ComposerPrimitive.Input
                addAttachmentOnPaste
                ref={inputRef}
                placeholder="Message, / for commands, @ to mention an agent"
                // `Input` spreads native textarea props, so the vendored slot
                // lets the theme's editor rules reach it like the landing brief.
                data-slot="composer-input"
                className={cn(
                  "max-h-32 min-h-11 w-full resize-none bg-transparent px-3 py-2.5 outline-none",
                  expanded && "max-h-none min-h-0 flex-1",
                )}
                rows={1}
                autoFocus
                enterKeyHint="send"
                aria-label="Message input"
              />
              <ComposerToolbar>
                <ComposerPrimitive.AddAttachment asChild>
                  <ComposerAttachButton aria-label="Add attachment" />
                </ComposerPrimitive.AddAttachment>
                <ComposerActions className="min-w-0">
                  <WorkspaceContext workspaceId={workspaceId} />
                  <ComposerModel workspaceId={workspaceId} />
                  {expandControl}
                  <NativeInterrupt workspaceId={workspaceId} />
                  <Dictation />
                  <SendOrCancel />
                </ComposerActions>
              </ComposerToolbar>
            </ComposerBar>
          </ComposerPrimitive.AttachmentDropzone>
        </ComposerPrimitive.Root>
      </Composer>
    </ComposerPrimitive.Unstable_TriggerPopoverRoot>
  );
}

/**
 * The `/` menu: Grove's closed command set, delivered through the same control
 * route the Controls tab uses, with the typed `/compact` stripped on execute.
 */
function useComposerCommands(workspaceId: string) {
  const invoke = useInvokeControl(workspaceId);
  const commands = useMemo(
    () =>
      COMPOSER_COMMANDS.map((command) => ({
        id: command.id,
        description: command.description,
        icon: "command",
        execute: () =>
          invoke.mutate(command.id, {
            // "Delivered", never "ran": the route answers when the control
            // reaches the session, not when the agent finishes compacting.
            onSuccess: () => toast.success(`/${command.id} delivered`),
            onError: (error) => toast.error(`Couldn’t deliver /${command.id}`, { description: error.message }),
          }),
      })),
    [invoke],
  );
  return unstable_useSlashCommandAdapter({ commands, removeOnExecute: true });
}

/** The `@` menu: every other live agent on this host, inserted as a directive. */
function useComposerMentions(workspaceId: string) {
  const contacts = useMailboxContacts();
  const items = useMemo(
    () =>
      mentionsFromContacts(contacts.data?.contacts, workspaceId).map((mention) => ({
        ...mention,
        icon: "agent",
      })),
    [contacts.data, workspaceId],
  );
  // `items` is always passed, so the adapter never falls back to listing the
  // thread's model-context tools — Grove registers none.
  return unstable_useMentionAdapter({ items });
}

/**
 * The context-window ring, from the primary session's measured reading.
 *
 * Renders nothing until the provider has reported both counts: a ring at 0% of
 * an invented window is a claim, and an absent reading is not zero.
 */
function WorkspaceContext({ workspaceId }: { readonly workspaceId: string }): ReactNode {
  const { data } = useWorkspaceActivity(workspaceId);
  const context = data?.sessions[0]?.activity.context;
  if (!context || context.size <= 0) return null;
  return (
    <ContextDisplay.Ring
      modelContextWindow={context.size}
      usage={{ totalTokens: context.used }}
      side="top"
    />
  );
}

/** Stop a native turn in flight. Offered by capability, never by `isRunning`,
 * which this runtime deliberately leaves false so a follow-up can always send. */
export function NativeInterrupt({ workspaceId }: { readonly workspaceId: string }): ReactNode {
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

/** Dictation, only where the runtime has a dictation adapter. */
function Dictation(): ReactNode {
  return (
    <AuiIf condition={(s) => s.thread.capabilities.dictation}>
      <AuiIf condition={(s) => s.composer.dictation == null}>
        <ComposerPrimitive.Dictate asChild>
          <TooltipIconButton tooltip="Voice input" side="top" aria-label="Start voice input">
            <MicIcon />
          </TooltipIconButton>
        </ComposerPrimitive.Dictate>
      </AuiIf>
      <AuiIf condition={(s) => s.composer.dictation != null}>
        <ComposerPrimitive.StopDictation asChild>
          <TooltipIconButton tooltip="Stop dictation" side="top" aria-label="Stop voice input">
            <SquareIcon className="fill-current" />
          </TooltipIconButton>
        </ComposerPrimitive.StopDictation>
      </AuiIf>
    </AuiIf>
  );
}

/** The reference's send/stop swap: exactly one of the two is mounted. */
function SendOrCancel(): ReactNode {
  const canSend = useAuiState((state) => state.composer.canSend);
  return (
    <>
      <AuiIf condition={(s) => !s.thread.isRunning}>
        <ComposerPrimitive.Send asChild>
          {/* The vendored `idle` means "nothing to send yet": it draws the dim
              fill, and the ink fill appears once there is a message. */}
          <ComposerSend streaming={false} idle={!canSend} className="aui-composer-send" />
        </ComposerPrimitive.Send>
      </AuiIf>
      <AuiIf condition={(s) => s.thread.isRunning}>
        <ComposerPrimitive.Cancel asChild>
          <ComposerSend streaming idle={false} className="aui-composer-cancel" />
        </ComposerPrimitive.Cancel>
      </AuiIf>
    </>
  );
}

/**
 * Per-workspace draft persistence plus the tour's prompt demand.
 *
 * TEXT goes back into the runtime; ATTACHMENTS do not. A browser cannot recover
 * a local file's bytes after a navigation, so a restored attachment is a
 * REMINDER to re-add it — pushing a zero-byte entry into the runtime would
 * render as staged and send a message promising a file that does not exist,
 * and re-adding it on every remount hands assistant-ui a duplicate id. Returns
 * the names still waiting to be re-added.
 */
function useDraftRestore(draftKey: string, expanded: boolean): readonly string[] {
  const aui = useAui();
  const composer = aui.composer;
  const text = useAuiState((state) => state.composer.text);
  const attachments = useAuiState((state) => state.composer.attachments);
  // It clears the moment a file of that name is staged again, so re-adding the
  // file is what dismisses the reminder rather than a separate ✕.
  const [pendingReAdd, setPendingReAdd] = useState<readonly string[]>([]);
  const staged = new Set(attachments.map(({ name }) => name));
  const outstanding = pendingReAdd.filter((name) => !staged.has(name));
  const savedAttachments = useMemo(
    () =>
      attachments.map((attachment) => ({
        name: attachment.name,
        contentType: attachment.contentType ?? "application/octet-stream",
      })),
    [attachments],
  );
  useComposerDraft({
    storageKey: draftKey,
    draft: {
      text,
      // Kept in the draft so the reminder survives a SECOND navigation.
      attachments: [...savedAttachments, ...reminders(outstanding)],
    },
    // Only restore into an editor the reader has not already started typing in.
    onRestore: ({ text: restoredText, attachments: restoredAttachments }) => {
      if (!aui.composer.getState().text) composer.setText(restoredText);
      setPendingReAdd(restoredAttachments.map(({ name }) => name));
    },
    onAcknowledged: () => setPendingReAdd([]),
  });

  // The onboarding tour writes its diagram query through the same `setText`.
  // Only the inline mount takes it, so the dialog's copy cannot write it twice.
  useWorkspaceOnboardingDemands(
    TOUR_KINDS,
    useCallback(
      (demand) => {
        if (!expanded) composer.setText(demand.text);
      },
      [composer, expanded],
    ),
  );

  useEffect(() => {
    setPendingReAdd((current) => {
      const next = current.filter((name) => !savedAttachments.some((attachment) => attachment.name === name));
      if (next.length === current.length) return current;
      // A re-added file dismisses its own reminder immediately, ahead of the
      // debounce, so navigating within that window cannot restore it.
      flushComposerDraft(draftKey, {
        text: aui.composer.getState().text,
        attachments: [...savedAttachments, ...reminders(next)],
      });
      return next;
    });
  }, [aui, draftKey, savedAttachments]);

  return outstanding;
}

/** A saved attachment entry that names a file the reader still has to re-add. */
function reminders(names: readonly string[]) {
  return names.map((name) => ({ name, contentType: "application/octet-stream", pendingReAdd: true }));
}
