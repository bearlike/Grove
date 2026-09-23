"use client";

import {
  AttachmentPrimitive,
  ComposerPrimitive,
  MessagePrimitive,
  useAui,
  useAuiState,
} from "@assistant-ui/react";

import { useAnnotationUi } from "@/components/grove/annotation";
import { AttachmentFile } from "@/components/grove/attachment-file";
import { ComposerAttachments } from "@/components/elements/composer";
import { filePartSize } from "@/lib/grove/adapters";

/**
 * The runtime's attachments — staged on the composer, or carried by a sent
 * message — as one shared `AttachmentFile` row each.
 *
 * NOTHING HERE DECIDES WHAT A FILE LOOKS LIKE. The row, its icon rule, its
 * truncation and its three states are `grove/attachment-file`'s, which the
 * landing composer composes too — so a file staged on the landing page, the
 * same file staged here, and the same file read back off a sent turn draw
 * identically. This file owns exactly one thing: translating assistant-ui's
 * attachment state into that row's props.
 *
 * The iteration and the scope stay `ComposerPrimitive` / `MessagePrimitive` /
 * `AttachmentPrimitive`, because they are the runtime's — and the runtime is
 * precisely what the landing surface deliberately does not mount. The vendored
 * `ComposerAttachments` supplies the list's own spacing, so neither surface
 * hand-rolls a row of tiles.
 */
export function ComposerAttachmentRows() {
  return (
    <ComposerAttachments className="w-full empty:hidden">
      <ComposerPrimitive.Attachments>
        {() => <ComposerAttachmentRow />}
      </ComposerPrimitive.Attachments>
    </ComposerAttachments>
  );
}

/**
 * A sent message's files, ABOVE its bubble.
 *
 * The user-message grid's first row is where upstream's own `Thread` puts a
 * message's attachments, and it sits outside the bubble's six-line clamp — so
 * every file stays visible while the prompt beneath it is collapsed, which is
 * the whole reason the transcript adapter builds `attachments` rather than
 * `file` content parts. Right-aligned to the bubble it belongs to.
 */
export function MessageAttachmentRows() {
  return (
    <ComposerAttachments className="col-span-full col-start-1 row-start-1 w-full justify-end empty:hidden">
      <MessagePrimitive.Attachments>
        {() => <MessageAttachmentRow />}
      </MessagePrimitive.Attachments>
    </ComposerAttachments>
  );
}

/** The byte count on the one file part every Grove attachment carries, or
 * null while the upload is still pending and no part exists yet. */
function useAttachmentPartSize(): number | null {
  return useAuiState((s) => {
    const part = s.attachment.content?.find((candidate) => candidate.type === "file");
    return part ? filePartSize(part) : null;
  });
}

function MessageAttachmentRow() {
  const name = useAuiState((s) => s.attachment.name);
  const contentType = useAuiState((s) => s.attachment.contentType);
  const size = useAttachmentPartSize();
  return (
    <AttachmentPrimitive.Root>
      <AttachmentFile name={name} contentType={contentType} size={size} />
    </AttachmentPrimitive.Root>
  );
}

function ComposerAttachmentRow() {
  const aui = useAui();
  const openAnnotator = useAnnotationUi((state) => state.open);
  const name = useAuiState((s) => s.attachment.name);
  // `contentType` is what the adapter recorded from the browser's own guess;
  // the filename is the fallback for a drop the browser could not type. Both
  // feed the SAME shared rule, so a row can never disagree with the transcript
  // row the same file becomes after it is sent.
  const contentType = useAuiState((s) => s.attachment.contentType);
  const status = useAuiState((s) => s.attachment.status.type);
  const failed = useAuiState(
    (s) =>
      s.attachment.status.type === "incomplete" &&
      s.attachment.status.reason === "error",
  );
  // The staged `File` knows the byte count until the upload completes; after
  // that the completed part carries it. Neither is invented: a row with no
  // size states none.
  const stagedSize = useAuiState((s) => s.attachment.file?.size);
  const partSize = useAttachmentPartSize();
  // The `File` is only on the row while the attachment is pending; once the
  // upload completes there is nothing left to overwrite, and the card's own
  // `state` gate already withholds the verb during the upload itself.
  const staged = useAuiState((s) => s.attachment.file);

  return (
    <AttachmentPrimitive.Root>
      <AttachmentFile
        name={name}
        contentType={contentType}
        size={stagedSize ?? partSize}
        state={status === "running" ? "uploading" : failed ? "error" : "done"}
        // REMOVED BY THE RUNTIME'S OWN SCOPED VERB, NEVER BY FILENAME. Two
        // staged files may legitimately carry one name, so a name-keyed remove
        // deletes whichever the runtime finds first — the reader sees a row
        // vanish, just not the row they clicked. `aui.attachment` is already
        // scoped to THIS row by the enclosing provider, so its `remove()` needs
        // no key at all, which is what the shared row's zero-argument
        // `onRemove` exists to take.
        onRemove={() => void aui.attachment().remove()}
        // REPLACE IS REMOVE-THEN-ADD, because the runtime has no in-place
        // verb: the annotated row therefore lands at the END of the list.
        // Both halves are the runtime's own, so the adapter's size refusal
        // still applies to the rasterized PNG exactly as to a picked file.
        onEdit={
          staged
            ? () =>
                openAnnotator({
                  file: staged,
                  onSave: (annotated) => {
                    void aui.attachment().remove();
                    void aui.composer().addAttachment(annotated);
                  },
                })
            : undefined
        }
      />
    </AttachmentPrimitive.Root>
  );
}
