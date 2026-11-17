"use client";

import { useRef } from "react";

import {
  Composer,
  ComposerActions,
  ComposerBar,
  ComposerSend,
  ComposerToolbar,
} from "@/components/elements/composer";
import { Textarea } from "@/components/ui/textarea";
import { useHeightTween } from "@/components/grove/dictation-waveform";
import { LaunchAttachFiles, LaunchAttachmentChips } from "./composer-files";
import { useOpenPillKind } from "./control-pill";
import { LaunchControlRow } from "./controls/control-row";
import { EffortPill } from "./controls/effort-pill";
import { ModelPill } from "./controls/model-pill";
import { LaunchDictateButton, LaunchVoiceView } from "./launch-dictation";
import { useLaunchDraft } from "./launch-draft";
import { LAUNCH_TESTIDS, useLaunchControls } from "./launch-state";
import { LaunchSummary } from "./launch-summary";
import { LaunchTip } from "./launch-tip";
import { customModelError } from "@/lib/grove/adapters/launch";

/**
 * The task brief on the vendored `elements/composer` anatomy: attach, model and
 * effort left, dictate and send right, with the configuration shelf (and the
 * expand control) connected directly beneath. No assistant-ui runtime is
 * mounted on this route (see `useLaunchSubmit`), so plain state drives it.
 * While dictating, the voice view replaces both editor and toolbar; the draft
 * and staged files live above and are untouched.
 */
export function LaunchComposer({
  disabled,
  expanded,
  inputRef,
  expandControl,
  noteTestIds,
}: {
  readonly disabled: boolean;
  readonly expanded: boolean;
  readonly inputRef: React.RefObject<HTMLTextAreaElement | null>;
  readonly expandControl: React.ReactNode;
  /** The moved dialog retains its established error/refusal selectors on this one note. */
  readonly noteTestIds?: { readonly error: string; readonly refusal: string };
}): React.ReactNode {
  const { values } = useLaunchControls();
  const { launch, dictation } = useLaunchDraft();
  const openKind = useOpenPillKind();
  // The pill collapse between the editor and the recording strip, and back.
  const bar = useRef<HTMLDivElement | null>(null);
  useHeightTween(bar, dictation.phase === "idle" ? "text" : "voice");
  const voice = dictation.available && dictation.phase !== "idle" ? dictation.phase : null;
  const customModelInvalid =
    values.customModel && customModelError(values.model ?? "") !== null;
  const blocked = !launch.canSubmit || customModelInvalid;

  return (
    <Composer
      className={expanded ? "flex min-h-0 max-w-none flex-1 flex-col" : "flex max-w-none flex-col"}
      data-testid={LAUNCH_TESTIDS.composer}
    >
      <ComposerBar ref={bar} className={expanded ? "min-h-0 flex-1" : undefined}>
        <LaunchAttachmentChips
          files={launch.attachments}
          pendingReAdd={launch.pendingReAdd}
          onRemove={launch.removeFile}
          onReplace={(index, file) => void launch.replaceFile(index, file)}
        />
        {voice ? (
          <LaunchVoiceView
            phase={voice}
            level={dictation.level}
            onCancel={dictation.cancel}
            onDone={() => void dictation.done()}
            onSend={dictation.send}
          />
        ) : (
          <>
            <Textarea
              ref={inputRef}
              value={launch.prompt}
              onChange={(event) => launch.setPrompt(event.target.value)}
              onPaste={(event) => {
                const files = Array.from(event.clipboardData.files);
                if (disabled || files.length === 0) return;
                event.preventDefault();
                void launch.addFiles(files);
              }}
              onKeyDown={(event) => {
                // `isComposing`: Enter in an IME candidate window commits a character.
                if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
                event.preventDefault();
                launch.submit();
              }}
              placeholder="Describe the work you want to do..."
              className={
                expanded
                  ? "min-h-0 w-full flex-1 resize-none px-3"
                  : "max-h-72 min-h-32 w-full resize-none px-3"
              }
              autoFocus
              disabled={disabled}
              enterKeyHint="send"
              aria-label="Task brief"
              data-testid={LAUNCH_TESTIDS.input}
            />
            <ComposerToolbar>
              <ComposerActions className="min-w-0">
                <LaunchTip label="Attach files">
                  <LaunchAttachFiles onAdd={launch.addFiles} disabled={disabled} />
                </LaunchTip>
                <LaunchTip label="Model" suppressed={openKind === "model"}>
                  <ModelPill />
                </LaunchTip>
                <LaunchTip label="Reasoning effort" suppressed={openKind === "effort"}>
                  <EffortPill />
                </LaunchTip>
              </ComposerActions>
              <ComposerActions>
                <LaunchDictateButton available={dictation.available} onStart={dictation.start} />
                <LaunchTip label={launch.isPending ? "Creating workspace" : "Send"}>
                  <ComposerSend
                    streaming={false}
                    aria-label={launch.isPending ? "Creating workspace" : "Send message"}
                    aria-busy={launch.isPending}
                    idle={blocked}
                    disabled={blocked}
                    onClick={launch.submit}
                  />
                </LaunchTip>
              </ComposerActions>
            </ComposerToolbar>
          </>
        )}
      </ComposerBar>
      <ComposerToolbar className="composer-shelf">
        <LaunchControlRow />
        {voice ? null : expandControl}
      </ComposerToolbar>
      <LaunchSummary testIds={noteTestIds} />
    </Composer>
  );
}
