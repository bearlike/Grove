"use client";

import type { ReactNode } from "react";
import { PencilLineIcon, XIcon } from "lucide-react";

import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { File } from "@/components/assistant-ui/file";
import { mimeFromName } from "@/lib/grove/adapters";

/**
 * ONE file card — staged on either composer, read back off a sent turn.
 *
 * It is the vendored `File` element and not `ComposerAttachmentChip`, the other
 * file vocabulary assistant-ui ships, because `File` is what the transcript
 * renders: a chip on the composer meant one file drew two ways across a send.
 * Colour and radius are the theme's, through `.attachment-card`.
 */
export type AttachmentFileState = "done" | "uploading" | "error";

/**
 * The one word that types a file, deliberately as coarse as `File.Icon`'s own
 * table and derived from the same input — so the word and the glyph cannot
 * answer "what kind of thing is this" differently.
 */
function kindLabel(mimeType: string): string {
  const type = mimeType.toLowerCase();
  if (type.startsWith("image/")) return "Image";
  if (type === "application/pdf") return "PDF";
  if (type === "application/json") return "JSON";
  if (type.startsWith("text/")) return "Text";
  if (type.startsWith("audio/")) return "Audio";
  if (type.startsWith("video/")) return "Video";
  return "File";
}

export function AttachmentFile({
  name,
  contentType,
  size,
  state = "done",
  onEdit,
  onRemove,
}: {
  readonly name: string;
  /** The browser's recorded guess; the filename is the fallback, through the
   * same rule every surface uses, so one file never draws two icons. */
  readonly contentType?: string | undefined;
  /** Decoded bytes, or absent where nothing weighed the file — the card says so
   * in words rather than stating a number nobody measured. */
  readonly size?: number | null | undefined;
  readonly state?: AttachmentFileState;
  /** Takes no argument on purpose: two staged files may share a name, so the
   * caller closes over the identity it actually holds (an index, a runtime
   * scope), never the filename. */
  readonly onRemove?: (() => void) | undefined;
  /** Open this file for annotation. Same zero-argument shape as `onRemove`,
   * for the same reason; the card only draws it for an image that is not
   * mid-upload, because the editor rasterizes pixels and a file whose bytes
   * are already leaving cannot be overwritten in place. */
  readonly onEdit?: (() => void) | undefined;
}): ReactNode {
  const mimeType = contentType || mimeFromName(name);
  const editable = state === "done" && onEdit !== undefined && mimeType.startsWith("image/");
  return (
    <File.Root
      size="sm"
      data-state={state}
      // Several facts about one object, not a control — and an unnamed group is
      // announced as nothing, so the filename names it.
      role="group"
      aria-label={name}
      // ONE WIDTH ON EVERY SURFACE: `File.Root` otherwise sizes to its content,
      // so a staged card and a sent one differed by the remove button only one
      // of them has. `max-w-full` is what still lets it shrink in a narrow pane.
      className="attachment-card w-72 min-w-0 max-w-full"
    >
      <File.Icon mimeType={mimeType} />
      <span className="flex min-w-0 flex-1 flex-col">
        {/* A TOOLTIP, NOT A `title`: the name is the one string that truncates,
            a native tooltip is mouse-only, and the two cannot be stacked
            because they race (`fleet/badges.tsx`). The trigger is the WRAPPER —
            `asChild` merges its own `data-slot` over the child's, so putting it
            on `File.Name` renames that slot out of the contract. */}
        <TooltipProvider delayDuration={0}>
          <Tooltip>
            <TooltipTrigger asChild>
              <span tabIndex={0} className="flex min-w-0">
                <File.Name className="text-base">{name}</File.Name>
              </span>
            </TooltipTrigger>
            <TooltipContent side="top">{name}</TooltipContent>
          </Tooltip>
        </TooltipProvider>
        {/* ALWAYS PRESENT. `File.Size` formats a bare number, so an absent one
            must never reach it or the card reads `NaN MB` — and a card that
            simply dropped its caption changed height between two files and said
            nothing about why. A status JOINS this line rather than replacing
            the size: a failed upload you can still weigh is more informative. */}
        <span
          data-slot="file-metadata"
          className="text-content-tertiary flex min-w-0 flex-wrap items-center gap-x-1 text-sm"
        >
          <span>{kindLabel(mimeType)}</span>
          <span aria-hidden>·</span>
          {size == null ? <span>Size not recorded</span> : <File.Size bytes={size} />}
          {state === "done" ? null : (
            <>
              <span aria-hidden>·</span>
              <span className={state === "error" ? "text-destructive" : undefined}>
                {state === "error" ? "Upload failed" : "Uploading"}
              </span>
            </>
          )}
        </span>
      </span>
      {editable ? (
        <TooltipIconButton
          tooltip={`Annotate ${name}`}
          side="top"
          type="button"
          className="min-h-[24px] min-w-[24px] shrink-0 self-start"
          onClick={onEdit}
        >
          <PencilLineIcon className="size-3.5" />
        </TooltipIconButton>
      ) : null}
      {state === "uploading" || !onRemove ? null : (
        <TooltipIconButton
          tooltip={`Remove ${name}`}
          side="top"
          type="button"
          // The vendored icon button is `size-6` — 19.2px at this root — and a
          // POINTER does not shrink with the type ramp (design-system §1).
          className="min-h-[24px] min-w-[24px] shrink-0 self-start"
          onClick={onRemove}
        >
          <XIcon className="size-3.5" />
        </TooltipIconButton>
      )}
    </File.Root>
  );
}
