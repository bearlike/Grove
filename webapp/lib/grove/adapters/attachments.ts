import type { CompleteAttachment, ThreadUserMessagePart } from "@assistant-ui/react";

import type { AttachmentUploadRequest } from "@/lib/grove/api";

/**
 * The per-file ceiling, in DECODED bytes.
 *
 * Duplicated from `AttachmentStore.MAX_BYTES` rather than fetched, because the
 * value's job is to produce a sentence beside the composer BEFORE a 32 MiB
 * base64 body is built and posted. A drift makes the client refuse a file the
 * daemon would have taken, which is the safe direction.
 *
 * IT LIVES HERE, not beside the `AttachmentAdapter` that used to own it,
 * because the landing composer refuses a file from `buildCreateRequest` — a
 * pure adapter that must not pull `groveClient` into its module graph. The
 * runtime re-exports all three, so nothing else moved.
 */
export const MAX_ATTACHMENT_BYTES = 32 * 1024 * 1024;

/**
 * How many files may ride one request.
 *
 * Mirrors `CreateWorkspaceRequest.attachments`' `max_length`. The workspace
 * composer has no equivalent because it uploads one file per request; this cap
 * exists because a create carries the whole set in one body.
 */
export const MAX_ATTACHMENTS = 20;

/**
 * What the file picker offers.
 *
 * Images and text/document files: the two things an agent can actually do
 * something with when handed a path. `accept` is a hint the browser applies to
 * the picker, never a guarantee — a drag-and-drop can carry anything, which is
 * why the size check below is a real check rather than a second hint.
 */
export const ATTACHMENT_ACCEPT =
  "image/*,text/*,application/pdf,application/json,application/xml,.md,.log,.diff,.patch,.yaml,.yml,.toml,.csv";

/** Why this file cannot be attached, or null when it can. */
export function attachmentError(file: { name: string; size: number }): string | null {
  if (file.size <= MAX_ATTACHMENT_BYTES) return null;
  const limit = Math.round(MAX_ATTACHMENT_BYTES / (1024 * 1024));
  return `${file.name} is larger than the ${limit} MB per-file attachment limit.`;
}

/** Why this many files cannot ride one create, or null when they can. */
export function attachmentCountError(count: number): string | null {
  if (count <= MAX_ATTACHMENTS) return null;
  return `Attach at most ${MAX_ATTACHMENTS} files to one workspace.`;
}

/**
 * One file staged on the landing composer, already read into the wire's encoding.
 *
 * WHY THE BYTES ARE HELD RATHER THAN UPLOADED. The workspace composer defers
 * its bytes to send, so an abandoned draft leaves no files behind; the landing
 * composer has nowhere to defer them TO, because the workspace it would upload
 * against is the thing the request creates. So a staged file is read once, at
 * pick time, and rides the create.
 *
 * `size` is the DECODED length and is not on the wire — it is what
 * `attachmentError` refuses against and what the row displays, so
 * `buildCreateRequest` drops it rather than sending a field the create contract
 * forbids.
 */
export interface StagedAttachment extends AttachmentUploadRequest {
  readonly size: number;
  /** The `File` this row was read from, while the visit that picked it lasts.
   * Not on the wire, and absent for a row a fixture or a restore built — it is
   * what lets an annotation re-edit find the markers it drew (they are keyed
   * by `File` identity) rather than reopening a flattened copy. */
  readonly file?: File;
}

const ANNOTATED_SUFFIX = ".annotated";

/**
 * How an annotated image is rendered: ONE lossy codec at a BOUNDED size.
 *
 * Annotation is decode → raster → re-encode, so the output's size is a
 * function of the PIXEL COUNT, never of the input file's size: a 400 KB
 * 3000x2400 JPEG is 29 MB of pixels the moment it enters the editor, and the
 * first cut rendered every one of them losslessly — 19 MB out. Both factors
 * are bounded here. The long edge is capped because the consumer is a vision
 * model that downsamples to ~1.2 MP anyway, so pixels past the cap are
 * rendered for nobody; 2048 keeps a screenshot's text legible with room to
 * spare. The codec is lossy WebP at a quality above a phone camera's own
 * JPEG, so a lossy source is not visibly re-lost and a screenshot's text edges
 * survive.
 */
export const ANNOTATION_CODEC = { mimeType: "image/webp", quality: 0.9 } as const;

export const MAX_ANNOTATED_EDGE = 2048;

/** The rendered size for a source of `width` x `height`: never upscaled, the
 * long edge never past `MAX_ANNOTATED_EDGE`, aspect kept, whole pixels. */
export function annotatedSize(
  width: number,
  height: number,
): { readonly width: number; readonly height: number } {
  const scale = Math.min(1, MAX_ANNOTATED_EDGE / Math.max(width, height));
  return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) };
}

/**
 * The MIME type a data URL actually carries.
 *
 * `toDataURL` SILENTLY substitutes PNG when the browser cannot encode the
 * requested type (per spec, not a bug), so the name an annotated file gets
 * must come from the bytes produced, never from the codec asked for — or a
 * browser without a WebP encoder ships PNG bytes under a `.webp` name.
 */
export function dataUrlMimeType(dataUrl: string): string {
  const match = /^data:([^;,]+)/.exec(dataUrl);
  return match?.[1] ?? "application/octet-stream";
}

/** The extension for what `dataUrlMimeType` answered — the two the encoder can emit. */
export function annotatedExtension(mimeType: string): "webp" | "png" {
  return mimeType === "image/webp" ? "webp" : "png";
}

/**
 * The name an annotated image is saved back under.
 *
 * The save OVERWRITES the staged file, so the name is the only thing that
 * says the bytes are no longer the ones the user picked: `shot.png` becomes
 * `shot.annotated.webp`. The extension is the BYTES', never the source's,
 * because a name that lies about its bytes misleads every reader that trusts
 * it, the engine's MIME table included. Idempotent, so a second edit never
 * grows the name.
 */
export function annotatedName(name: string, extension: "webp" | "png"): string {
  const dot = name.lastIndexOf(".");
  const stem = dot > 0 ? name.slice(0, dot) : name;
  const base = stem.endsWith(ANNOTATED_SUFFIX) ? stem.slice(0, -ANNOTATED_SUFFIX.length) : stem;
  return `${base}${ANNOTATED_SUFFIX}.${extension}`;
}

/**
 * The `File` a staged landing-composer row was read from.
 *
 * The row keeps its `File` while it can; a row without one is rebuilt from
 * the wire's base64 — the inverse of `stageFile` — so an editor always gets
 * a `File` and the surface never holds a second representation beside the
 * first.
 */
export function fileFromStaged(staged: StagedAttachment): File {
  if (staged.file) return staged.file;
  const binary = atob(staged.content_base64);
  const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
  return new File([bytes], staged.name, { type: mimeFromName(staged.name) });
}

/**
 * A sent message's attachments, recovered from the transcript.
 *
 * WHY THIS PARSES PROSE, WHICH IS NORMALLY THE WRONG MOVE. `SessionTurnView`
 * carries no structured attachment field: the engine appends the file list to
 * the human's own text as a `<grove-instruction kind="attachments">` block,
 * because the agent's only way to use an attachment is to be told a path it can
 * open. So the transcript IS the record, and the block is the only place it
 * exists.
 *
 * That is affordable precisely because the fence is GROVE'S OWN, not a
 * provider's output — `GroveInstruction` in `core/instructions.py` defines the
 * tag, the kind and the row shape, and its whole reason for existing is that a
 * reader must be able to tell Grove's sentence from its user's. Parsing a
 * contract we publish is not the provider-boundary trap; parsing a model's
 * words would be.
 */

export interface MessageAttachment {
  readonly name: string;
  readonly path: string;
  /** Decoded bytes, or null for a transcript written before the engine
   * published the count — absent is stated, never invented. */
  readonly size: number | null;
}

export interface UserMessageBody {
  /** What the human actually typed, with Grove's block lifted off. */
  readonly text: string;
  readonly attachments: readonly MessageAttachment[];
}

/**
 * Mirrors `GroveInstruction.TAG` and its `attachments` kind.
 *
 * Anchored to the END because `GroveInstruction.append` puts the block after
 * the human's text and nowhere else. Without the anchor, a reader who pastes
 * the tag into their own prompt has their message silently rewritten.
 */
const ATTACHMENT_BLOCK =
  /\n*<grove-instruction kind="attachments">\n([\s\S]*?)\n<\/grove-instruction>\s*$/;

/**
 * One `- <name> — <path> (<size> bytes)` row.
 *
 * Non-greedy on the name and greedy on the path, because the engine sanitizes a
 * filename down to `[A-Za-z0-9._-]` and cannot leave an em dash in it, while a
 * path is whatever the workspace's own directory is called. The size suffix is
 * OPTIONAL: transcripts written before the engine published it still parse,
 * with `size: null`.
 */
const ROW = /^-\s+(.+?)\s+—\s+(.+?)(?:\s+\((\d+) bytes\))?$/;

/** Split Grove's attachment block off a user turn's text. */
export function splitAttachments(userText: string): UserMessageBody {
  const match = ATTACHMENT_BLOCK.exec(userText);
  if (!match) return { text: userText, attachments: [] };

  const attachments: MessageAttachment[] = [];
  for (const line of match[1].split("\n")) {
    const row = ROW.exec(line.trim());
    if (row) {
      attachments.push({
        name: row[1],
        path: row[2],
        size: row[3] === undefined ? null : Number(row[3]),
      });
    }
  }
  // A block that parsed to nothing is left in the text rather than deleted: it
  // is still something the agent was told, and silently dropping it would hide
  // the message the reader is looking at.
  if (attachments.length === 0) return { text: userText, attachments: [] };
  return { text: userText.slice(0, match.index).trimEnd(), attachments };
}

/**
 * Extension → MIME, for the eight branches the vendored `getMimeTypeIcon`
 * actually distinguishes.
 *
 * A table rather than a lookup library: the engine records a filename and no
 * content type, the browser's own guess is unavailable once the file has left
 * for the daemon, and the icon vocabulary is fixed by the vendored component.
 * Anything unlisted resolves to the generic file glyph, which is the honest
 * answer for a type we cannot name.
 */
const MIME_BY_EXTENSION: Readonly<Record<string, string>> = {
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  gif: "image/gif",
  webp: "image/webp",
  svg: "image/svg+xml",
  bmp: "image/bmp",
  ico: "image/x-icon",
  avif: "image/avif",
  heic: "image/heic",
  pdf: "application/pdf",
  json: "application/json",
  jsonl: "application/json",
  txt: "text/plain",
  log: "text/plain",
  md: "text/markdown",
  csv: "text/csv",
  tsv: "text/tab-separated-values",
  html: "text/html",
  css: "text/css",
  xml: "text/xml",
  yaml: "text/yaml",
  yml: "text/yaml",
  toml: "text/plain",
  ini: "text/plain",
  diff: "text/x-diff",
  patch: "text/x-diff",
  ts: "text/plain",
  tsx: "text/plain",
  js: "text/plain",
  jsx: "text/plain",
  py: "text/plain",
  rs: "text/plain",
  go: "text/plain",
  sh: "text/plain",
  sql: "text/plain",
  mp3: "audio/mpeg",
  wav: "audio/wav",
  ogg: "audio/ogg",
  m4a: "audio/mp4",
  flac: "audio/flac",
  mp4: "video/mp4",
  mov: "video/quicktime",
  webm: "video/webm",
  mkv: "video/x-matroska",
};

/** The MIME type a filename implies, or a generic one when it implies none. */
export function mimeFromName(name: string): string {
  const dot = name.lastIndexOf(".");
  const extension = dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
  return MIME_BY_EXTENSION[extension] ?? "application/octet-stream";
}

/**
 * One complete assistant-ui attachment per file, for `message.attachments`.
 *
 * ATTACHMENTS, NOT CONTENT PARTS, because the two render in different places:
 * a content part lands inside the user bubble after the text, where the
 * six-line clamp hides it first, while `message.attachments` is what the
 * thread's user-message grid reserves its first row for — above the bubble,
 * outside the clamp. That is also the shape a message the composer just sent
 * carries, so a file reads identically before and after the round trip.
 *
 * `sourceType: "id"` is literally true: the path addresses the file inside the
 * AGENT's namespace, which for a containerized workspace is not a path this
 * browser could fetch, so nothing here ever becomes a download link.
 *
 * `size` rides `content[0].providerMetadata.grove` because assistant-ui's
 * file part has no size field of its own and that map is the one slot the
 * type reserves for a provider's extra facts. A file whose size was never
 * published carries no entry at all rather than a zero.
 */
export function messageAttachments(
  attachments: readonly MessageAttachment[],
): CompleteAttachment[] {
  return attachments.map((attachment, index) => {
    const mimeType = mimeFromName(attachment.name);
    return {
      id: `${attachment.path}#${index}`,
      type: attachmentKind(mimeType),
      name: attachment.name,
      contentType: mimeType,
      status: { type: "complete" },
      content: [
        {
          type: "file" as const,
          filename: attachment.name,
          data: attachment.path,
          mimeType,
          sourceType: "id" as const,
          ...(attachment.size == null
            ? {}
            : { providerMetadata: { grove: { size: attachment.size } } }),
        },
      ],
    };
  });
}

/** assistant-ui's own attachment kinds, from a MIME type. */
export function attachmentKind(mimeType: string): "image" | "document" | "file" {
  if (mimeType.startsWith("image/")) return "image";
  if (mimeType.startsWith("text/") || mimeType.startsWith("application/")) return "document";
  return "file";
}

/**
 * The byte count a file part carries, if any surface ever recorded one.
 *
 * The staged `File` knows it on the composer; the engine's row publishes it on
 * a sent turn; an upload completed against the daemon does not carry it back.
 * Null on every path that never weighed the file.
 */
export function filePartSize(part: ThreadUserMessagePart): number | null {
  if (part.type !== "file") return null;
  const size = part.providerMetadata?.grove?.size;
  return typeof size === "number" ? size : null;
}
