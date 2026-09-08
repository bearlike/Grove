"use client";

import type {
  AttachmentAdapter,
  CompleteAttachment,
  PendingAttachment,
} from "@assistant-ui/react";

import type { AttachmentView } from "@/lib/grove/api";
import {
  ATTACHMENT_ACCEPT,
  attachmentError,
  attachmentKind,
  MAX_ATTACHMENT_BYTES,
} from "@/lib/grove/adapters/attachments";
import { groveClient } from "@/lib/grove/hooks/client";
import { refusalNotice } from "./notice";

// The three pure values moved down to `adapters/attachments.ts` so the landing
// composer's `buildCreateRequest` — a pure adapter — can refuse a file without
// importing this module's `groveClient`. Re-exported here because this is the
// name every existing caller already imports.
export { ATTACHMENT_ACCEPT, attachmentError, attachmentKind, MAX_ATTACHMENT_BYTES };

/**
 * The composer's file attachments, as an assistant-ui `AttachmentAdapter`.
 *
 * The composer has rendered `ComposerAttachments` and an add button since it
 * was ported, with no adapter behind them — so every file drop threw
 * "Attachments are not supported". This is that adapter, and the whole of it is
 * two daemon calls: store the bytes, then carry the id the daemon answered with
 * onto the message that names it.
 *
 * WHY THE ID AND NOT THE PATH. The upload answers with both; `path` is where
 * the AGENT will read the file, already translated into its own namespace, and
 * only the engine knows which namespace that is. The client shows it and never
 * sends it back.
 */

/**
 * The stored-attachment ids a composed message names.
 *
 * They ride on `message.attachments`, NOT in `message.content` — assistant-ui's
 * composer builds the append with the text parts alone and hands the completed
 * attachments across in their own field. Each one carries its id as a `file`
 * part whose `sourceType` is `"id"`, which is exactly what that discriminator
 * means, so nothing bespoke is smuggled through a content part.
 */
export function attachmentIds(
  attachments: readonly CompleteAttachment[] | undefined,
): string[] {
  return (attachments ?? []).flatMap((attachment) =>
    (attachment.content ?? []).flatMap((part) =>
      part.type === "file" && part.sourceType === "id" ? [part.data] : [],
    ),
  );
}

/** One pending attachment, completed against what the daemon stored. */
export function completeAttachment(
  pending: PendingAttachment,
  view: AttachmentView,
): CompleteAttachment {
  return {
    ...pending,
    status: { type: "complete" },
    content: [
      {
        type: "file",
        filename: view.name,
        data: view.id,
        mimeType: pending.file.type || "application/octet-stream",
        sourceType: "id",
        // The staged File is the only thing that knows the byte count, and
        // it leaves the runtime with the upload — so the size rides the part
        // the way the engine's row publishes it for a sent turn.
        providerMetadata: { grove: { size: pending.file.size } },
      },
    ],
  };
}

/**
 * Bind the adapter to one workspace.
 *
 * `onRefusal` exists because assistant-ui's composer swallows an upload
 * failure into `console.error` and silently restores the draft — which reads as
 * the send button doing nothing. The notice beside the composer is the only
 * place a reader learns why, so both the size refusal and the daemon's own
 * refusal report through it before the rejection propagates.
 */
export function groveAttachmentAdapter(
  workspaceId: string,
  onRefusal: (message: string) => void,
): AttachmentAdapter {
  return {
    accept: ATTACHMENT_ACCEPT,

    async add({ file }: { file: File }): Promise<PendingAttachment> {
      const refusal = attachmentError(file);
      if (refusal) {
        onRefusal(refusal);
        throw new Error(refusal);
      }
      return {
        id: `${file.name}-${file.size}-${file.lastModified}`,
        type: attachmentKind(file.type),
        name: file.name,
        contentType: file.type || "application/octet-stream",
        file,
        // The bytes are uploaded on send, not on add: a draft that is never
        // sent must not leave files behind in the workspace.
        status: { type: "requires-action", reason: "composer-send" },
      };
    },

    async send(attachment: PendingAttachment): Promise<CompleteAttachment> {
      try {
        const view = await groveClient.uploadAttachment(
          workspaceId,
          attachment.name,
          attachment.file,
        );
        return completeAttachment(attachment, view);
      } catch (error) {
        onRefusal(refusalNotice(error, "attach"));
        throw error;
      }
    },

    // Nothing to undo: an attachment removed from the draft was never uploaded,
    // and one already uploaded is a file in the workspace the agent may already
    // have been told about.
    async remove(): Promise<void> {},
  };
}
