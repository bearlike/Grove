import { readFileSync } from "node:fs";
import { fromThreadMessageLike } from "@assistant-ui/react";
import { describe, expect, it } from "vitest";

import {
  filePartSize,
  messageAttachments,
  mimeFromName,
  messagesFromTurns,
  splitAttachments,
} from "@/lib/grove/adapters";
import type { SessionTurnView } from "@/lib/grove/api";

/**
 * Attachments on a SENT message.
 *
 * The daemon has no structured field for them — it appends Grove's own fenced
 * block to the human's text — so the transcript is the record, and lifting the
 * files back out of it is what makes "which files went with which message"
 * answerable at all.
 */

const BLOCK = [
  '<grove-instruction kind="attachments">',
  "The user attached 2 files to the message above. Each is on disk in this workspace at the path shown; read whichever you need.",
  "",
  "- trace.log — /workspace/.grove/attachments/7f3c9a1b2c4d/trace.log (2048 bytes)",
  "- diagram.png — /workspace/.grove/attachments/0011aabbccdd/diagram.png",
  "</grove-instruction>",
].join("\n");

function turn(userText: string): SessionTurnView {
  return {
    index: 0,
    started_at: "2026-09-07T10:00:00Z",
    user_text: userText,
    entries: [],
  } as unknown as SessionTurnView;
}

describe("splitAttachments", () => {
  it("lifts the block off and keeps the human's own words", () => {
    const body = splitAttachments(`Have a look at these.\n\n${BLOCK}`);
    expect(body.text).toBe("Have a look at these.");
    // The size suffix is what the engine publishes now; a row without one is
    // a transcript written before it did, and its size is stated as absent.
    expect(body.attachments).toEqual([
      { name: "trace.log", path: "/workspace/.grove/attachments/7f3c9a1b2c4d/trace.log", size: 2048 },
      { name: "diagram.png", path: "/workspace/.grove/attachments/0011aabbccdd/diagram.png", size: null },
    ]);
  });

  it("leaves an ordinary message untouched", () => {
    expect(splitAttachments("run the tests")).toEqual({
      text: "run the tests",
      attachments: [],
    });
  });

  it("recovers an attachment-only message, whose text is just the block", () => {
    const body = splitAttachments(BLOCK);
    expect(body.text).toBe("");
    expect(body.attachments).toHaveLength(2);
  });

  it("is anchored to the END, so a reader quoting the tag keeps their own prose", () => {
    // `GroveInstruction.append` puts the block after the human's text and
    // nowhere else, so a tag in the middle is the user's, not Grove's.
    const quoted = `${BLOCK}\n\nwhat does that block mean?`;
    expect(splitAttachments(quoted)).toEqual({ text: quoted, attachments: [] });
  });

  it("keeps a block it could not parse rather than deleting the message body", () => {
    const empty = '<grove-instruction kind="attachments">\nnothing here\n</grove-instruction>';
    expect(splitAttachments(empty).text).toBe(empty);
  });
});

describe("mimeFromName", () => {
  it("names the types the vendored File element actually distinguishes", () => {
    expect(mimeFromName("diagram.png")).toBe("image/png");
    expect(mimeFromName("spec.pdf")).toBe("application/pdf");
    expect(mimeFromName("data.json")).toBe("application/json");
    expect(mimeFromName("trace.log")).toBe("text/plain");
    expect(mimeFromName("clip.mp4")).toBe("video/mp4");
    expect(mimeFromName("take.wav")).toBe("audio/wav");
  });

  it("is case-insensitive on the extension", () => {
    expect(mimeFromName("SHOT.PNG")).toBe("image/png");
  });

  it("answers generically rather than guessing when the name implies nothing", () => {
    expect(mimeFromName("Makefile")).toBe("application/octet-stream");
    expect(mimeFromName(".hidden")).toBe("application/octet-stream");
    expect(mimeFromName("core.dump")).toBe("application/octet-stream");
  });
});

describe("messageAttachments", () => {
  it("builds a COMPLETE attachment whose file part is an ID reference", () => {
    // The path addresses the file in the AGENT's namespace — inside a container
    // for a container workspace — so it is not something this browser can
    // fetch. `sourceType: "id"` says exactly that, and it is the same shape
    // the composer's adapter completes an upload into.
    const [attachment] = messageAttachments([
      { name: "trace.log", path: "/w/.grove/a/1/trace.log", size: 512 },
    ]);
    expect(attachment).toMatchObject({
      type: "document",
      name: "trace.log",
      contentType: "text/plain",
      status: { type: "complete" },
      content: [
        {
          type: "file",
          filename: "trace.log",
          data: "/w/.grove/a/1/trace.log",
          mimeType: "text/plain",
          sourceType: "id",
          providerMetadata: { grove: { size: 512 } },
        },
      ],
    });
    expect(filePartSize(attachment.content[0]!)).toBe(512);
  });

  it("carries NO size entry for a file nobody weighed, rather than a zero", () => {
    const [attachment] = messageAttachments([{ name: "old.txt", path: "/w/old.txt", size: null }]);
    expect(attachment.content[0]).not.toHaveProperty("providerMetadata");
    expect(filePartSize(attachment.content[0]!)).toBeNull();
  });

  it("keys two same-named files apart", () => {
    const [a, b] = messageAttachments([
      { name: "shot.png", path: "/w/1/shot.png", size: null },
      { name: "shot.png", path: "/w/2/shot.png", size: null },
    ]);
    expect(a.id).not.toBe(b.id);
  });
});

describe("messagesFromTurns", () => {
  it("preserves known and unrecorded sizes through assistant-ui's runtime conversion", () => {
    const [message] = messagesFromTurns([turn(`Review these.\n\n${BLOCK}`)]);
    const converted = fromThreadMessageLike(message, "attachment-message", {
      type: "complete",
      reason: "stop",
    });
    expect(converted.role).toBe("user");
    if (converted.role !== "user") throw new Error("Expected a user message");
    expect(converted.attachments.map((attachment) => filePartSize(attachment.content[0]!))).toEqual([
      2048,
      null,
    ]);
    expect(converted.attachments.map((attachment) => attachment.name)).toEqual([
      "trace.log",
      "diagram.png",
    ]);
  });

  it("carries a sent message's files as ATTACHMENTS, with the text alone in content", () => {
    // Attachments render in the user-message grid's first row, above the
    // bubble and outside its clamp; a `file` content part would render inside
    // the bubble after the text, which is the first thing the clamp hides.
    const [message] = messagesFromTurns([turn(`Have a look at these.\n\n${BLOCK}`)]);

    expect(message.role).toBe("user");
    expect(message.content).toEqual([{ type: "text", text: "Have a look at these." }]);
    expect(message.attachments?.map((attachment) => attachment.name)).toEqual([
      "trace.log",
      "diagram.png",
    ]);
    expect(message.attachments?.[1]).toMatchObject({ type: "image", contentType: "image/png" });
  });

  it("still renders a message that was only attachments", () => {
    const [message] = messagesFromTurns([turn(BLOCK)]);
    expect(message.role).toBe("user");
    expect(message.content).toEqual([]);
    expect(message.attachments).toHaveLength(2);
  });

  it("leaves a turn with no attachments byte-identical", () => {
    const [message] = messagesFromTurns([turn("run the tests")]);
    expect(message.content).toEqual([{ type: "text", text: "run the tests" }]);
    expect(message).not.toHaveProperty("attachments");
  });
});

/**
 * The icon rule is the VENDORED element's, in both places it is drawn.
 *
 * Pinned as a source census rather than a render, in `displayed-defaults`'
 * style: both surfaces need a live assistant-ui runtime to mount, and the
 * defect this guards against — somebody reaching for a lucide glyph directly —
 * is invisible in a render that has no attachments in it.
 */
const THREAD = "components/grove/workspace/thread.tsx";
const COMPOSER_ROW = "components/grove/workspace/composer-attachment.tsx";

function source(path: string): string {
  const text = readFileSync(path, "utf8");
  expect(text.length, `${path} is empty`).toBeGreaterThan(500);
  return text;
}

function code(path: string): string {
  return source(path)
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "");
}

describe("the File element is composed, never re-implemented", () => {
  it("renders a sent message's attachments through the runtime's own iteration, above the bubble", () => {
    const thread = code(THREAD);
    expect(thread).toContain("<MessageAttachmentRows />");
    // Above: the rows come before the content wrapper in the grid, and the
    // bubble registers no File part renderer because nothing lands in it.
    expect(thread.indexOf("<MessageAttachmentRows />")).toBeLessThan(
      thread.indexOf("aui-user-message-content-wrapper"),
    );
    expect(thread).not.toContain("components={{ File }}");

    const rows = code(COMPOSER_ROW);
    expect(rows).toContain("MessagePrimitive.Attachments");
    expect(rows).toContain("row-start-1");
  });

  it("delegates staged and sent files alike to the one shared File row", () => {
    const text = code(COMPOSER_ROW);
    expect(text).toContain('from "@/components/grove/attachment-file"');
    expect(text.match(/<AttachmentFile/g)).toHaveLength(2);
    expect(text).not.toMatch(/\bFileTextIcon\b|\bFileIcon\b|ComposerAttachmentChip/);
  });
});
