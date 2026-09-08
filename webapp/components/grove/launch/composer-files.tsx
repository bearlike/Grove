"use client";

import { useRef, type ReactNode } from "react";

import { useAnnotationUi } from "@/components/grove/annotation";
import { AttachmentFile } from "@/components/grove/attachment-file";
import { ComposerAttachments } from "@/components/grove/composer";
import { ComposerAttachButton } from "@/components/elements/composer";
import {
  ATTACHMENT_ACCEPT,
  fileFromStaged,
  mimeFromName,
  type StagedAttachment,
} from "@/lib/grove/adapters";
import { LAUNCH_TESTIDS } from "./launch-state";

/**
 * The landing composer's staged files, as `File` rows.
 *
 * WHY THIS IS PLAIN REACT WHERE THE WORKSPACE COMPOSER USES PRIMITIVES.
 * `ComposerPrimitive.Attachments`, `ComposerPrimitive.AddAttachment` and
 * `AttachmentPrimitive` all read assistant-ui's composer state, and this route
 * deliberately mounts no runtime — `useLaunchSubmit` records the navigation bug
 * that removing it fixed. So the scope and the remove verb are a list and a
 * callback here.
 *
 * WHAT IS SHARED IS THE PRESENTATION, NOT THE UPLOAD STATE, which is the part
 * that matters: `AttachmentFile` is the same row the session composer draws
 * and the same row a SENT turn draws, so one file is one thing wearing one
 * look on every surface it crosses.
 */
export function LaunchAttachmentChips({
  files,
  pendingReAdd = [],
  onRemove,
  onReplace,
}: {
  readonly files: readonly StagedAttachment[];
  readonly pendingReAdd?: readonly string[];
  readonly onRemove: (index: number) => void;
  /** Overwrite the row at `index` — what an annotation save hands back. */
  readonly onReplace: (index: number, file: File) => void;
}): ReactNode {
  const openAnnotator = useAnnotationUi((state) => state.open);
  if (files.length === 0 && pendingReAdd.length === 0) return null;
  return (
    <ComposerAttachments data-testid={LAUNCH_TESTIDS.attachments}>
      {files.map((file, index) => (
        <AttachmentFile
          key={`${index}-${file.name}`}
          name={file.name}
          // The name is the only type information a staged file carries here:
          // the browser's own `File.type` guess is not on the wire and would
          // give this chip a different answer from the transcript row the same
          // file becomes after it is sent.
          contentType={mimeFromName(file.name)}
          size={file.size}
          // Bytes are already in hand — a staged file is read once at pick time
          // and rides the create, because the workspace it would upload against
          // is the thing the request creates. There is no in-flight state to
          // report, so the row is never `uploading`.
          state="done"
          // REMOVAL BINDS THE INDEX, NOT THE NAME: picking `notes.txt` out of
          // two directories stages two perfectly valid rows, and removing by
          // name would drop the wrong one — or both.
          onRemove={() => onRemove(index)}
          // The staged row holds the wire's base64, and the editor wants the
          // `File` it came from; rebuilding it here keeps this surface holding
          // ONE representation of a file rather than two that can disagree.
          onEdit={() =>
            openAnnotator({
              file: fileFromStaged(file),
              onSave: (annotated) => onReplace(index, annotated),
            })
          }
        />
      ))}
      {pendingReAdd.map((name) => (
        // Metadata survived navigation, not file bytes. No upload was attempted.
        <p key={`re-add-${name}`} role="status" className="text-content-tertiary text-xs">
          <span>Re-add file</span>: <span className="text-content-secondary">{name}</span>
        </p>
      ))}
    </ComposerAttachments>
  );
}

/**
 * The add-a-file affordance, over a hidden native picker.
 *
 * `ComposerAttachButton` is the vendored control and it is runtime-free — a
 * plain button whose only requirement is an `onClick` — so the composer's own
 * add glyph is reused rather than approximated. It disables itself when handed
 * no handler, and props spread after that, so an explicit `disabled` still wins.
 */
export function LaunchAttachFiles({
  onAdd,
  disabled,
}: {
  readonly onAdd: (files: readonly globalThis.File[]) => Promise<void>;
  readonly disabled: boolean;
}): ReactNode {
  const picker = useRef<HTMLInputElement | null>(null);

  return (
    <>
      <input
        ref={picker}
        type="file"
        multiple
        accept={ATTACHMENT_ACCEPT}
        className="hidden"
        onChange={(event) => {
          const picked = Array.from(event.target.files ?? []);
          // Cleared before anything else: an input holding the same value fires
          // no `change`, so picking one file, removing its chip and picking it
          // again would silently do nothing.
          event.target.value = "";
          if (picked.length > 0) void onAdd(picked);
        }}
      />
      <ComposerAttachButton
        onClick={() => picker.current?.click()}
        disabled={disabled}
        data-testid={LAUNCH_TESTIDS.attach}
      />
    </>
  );
}
