import { describe, expect, it } from "vitest";

import { base64FromBytes } from "@/lib/grove/api";
import type { AttachmentView } from "@/lib/grove/api";
import {
  ANNOTATION_CODEC,
  annotatedExtension,
  annotatedName,
  annotatedSize,
  dataUrlMimeType,
  fileFromStaged,
  MAX_ANNOTATED_EDGE,
} from "@/lib/grove/adapters";
import {
  ATTACHMENT_ACCEPT,
  attachmentError,
  attachmentIds,
  attachmentKind,
  completeAttachment,
  MAX_ATTACHMENT_BYTES,
} from "@/lib/grove/runtime";

/**
 * The pure half of composer attachments.
 *
 * Everything here is a decision made before or after the one network call, so
 * it is exercisable without a daemon — the same split every adapter in
 * `lib/grove/adapters` follows.
 */

const VIEW: AttachmentView = {
  id: "att_7f3c",
  name: "trace.log",
  // Where the AGENT reads it. It is display-only, and the assertions below are
  // what pin that it never leaves the client again.
  path: "/workspace/.grove/attachments/att_7f3c/trace.log",
};

function pendingFile(name: string, type: string, bytes = 4): File {
  return new File([new Uint8Array(bytes)], name, { type });
}

describe("base64FromBytes", () => {
  it("matches btoa for a short payload", () => {
    const bytes = new TextEncoder().encode("hello grove");
    expect(base64FromBytes(bytes)).toBe(btoa("hello grove"));
  });

  it("round-trips every byte value, including the ones a text encoder would mangle", () => {
    const bytes = Uint8Array.from({ length: 256 }, (_, i) => i);
    const decoded = Uint8Array.from(atob(base64FromBytes(bytes)), (c) => c.charCodeAt(0));
    expect([...decoded]).toEqual([...bytes]);
  });

  it("survives a payload far past the argument-spread limit", () => {
    // The one-liner this replaces spreads one argument per byte and throws
    // RangeError somewhere around 100K — well under the daemon's own ceiling,
    // so the naive version fails on exactly the files the limit exists for.
    const bytes = new Uint8Array(1_000_000).fill(0x41);
    expect(base64FromBytes(bytes)).toHaveLength(Math.ceil(1_000_000 / 3) * 4);
  });
});

describe("attachmentError", () => {
  it("passes a file inside the daemon's own per-file ceiling", () => {
    expect(attachmentError({ name: "notes.md", size: MAX_ATTACHMENT_BYTES })).toBeNull();
  });

  it("names the file and the limit rather than dropping it silently", () => {
    const refusal = attachmentError({ name: "core.dump", size: MAX_ATTACHMENT_BYTES + 1 });
    expect(refusal).toContain("core.dump");
    expect(refusal).toContain("32 MB");
  });
});

describe("attachmentKind", () => {
  it("maps a mime type onto assistant-ui's three kinds", () => {
    expect(attachmentKind("image/png")).toBe("image");
    expect(attachmentKind("text/markdown")).toBe("document");
    expect(attachmentKind("application/pdf")).toBe("document");
    expect(attachmentKind("")).toBe("file");
  });

  it("offers images and documents in the picker, and says so in one place", () => {
    expect(ATTACHMENT_ACCEPT).toContain("image/*");
    expect(ATTACHMENT_ACCEPT).toContain("text/*");
  });
});

describe("completeAttachment", () => {
  const pending = {
    id: "local-1",
    type: "document" as const,
    name: "trace.log",
    contentType: "text/plain",
    file: pendingFile("trace.log", "text/plain"),
    status: { type: "requires-action" as const, reason: "composer-send" as const },
  };

  it("carries the daemon's ID as a file part, which is what sourceType id means", () => {
    expect(completeAttachment(pending, VIEW).content).toEqual([
      {
        type: "file",
        filename: "trace.log",
        data: "att_7f3c",
        mimeType: "text/plain",
        sourceType: "id",
        // The staged File leaves the runtime with the upload, so its byte
        // count rides the part — the same slot the engine's row fills on a
        // sent turn — and the row keeps stating a size after completion.
        providerMetadata: { grove: { size: pending.file.size } },
      },
    ]);
  });

  it("never carries the agent-side path back — only the engine owns that namespace", () => {
    expect(JSON.stringify(completeAttachment(pending, VIEW))).not.toContain(VIEW.path);
  });

  it("settles the upload state so the composer stops showing a spinner", () => {
    expect(completeAttachment(pending, VIEW).status).toEqual({ type: "complete" });
  });
});

describe("attachmentIds", () => {
  const complete = (id: string, name: string) =>
    completeAttachment(
      {
        id: `local-${id}`,
        type: "document" as const,
        name,
        file: pendingFile(name, "text/plain"),
        status: { type: "requires-action" as const, reason: "composer-send" as const },
      },
      { id, name, path: `/workspace/.grove/attachments/${id}/${name}` },
    );

  it("reads the ids off a composed message, in order", () => {
    expect(attachmentIds([complete("att_a", "a.txt"), complete("att_b", "b.txt")])).toEqual([
      "att_a",
      "att_b",
    ]);
  });

  it("is empty for a message that carried none — assistant-ui omits the field", () => {
    expect(attachmentIds(undefined)).toEqual([]);
    expect(attachmentIds([])).toEqual([]);
  });

  it("ignores a file part that is not an id reference", () => {
    // A data-URL attachment from some other adapter is not something the daemon
    // stored, so naming it would be inventing an id.
    expect(
      attachmentIds([
        {
          id: "local-x",
          type: "image",
          name: "shot.png",
          status: { type: "complete" },
          content: [{ type: "file", data: "data:image/png;base64,AA==", mimeType: "image/png" }],
        },
      ]),
    ).toEqual([]);
  });
});

describe("annotatedName", () => {
  it("marks a first edit by inserting `.annotated` and taking the BYTES' extension", () => {
    expect(annotatedName("shot.png", "webp")).toBe("shot.annotated.webp");
    expect(annotatedName("photo.JPG", "webp")).toBe("photo.annotated.webp");
    // A browser with no WebP encoder produced PNG bytes, so the name says PNG:
    // an extension that lies about the bytes misleads every reader that
    // trusts it, the engine's MIME table included.
    expect(annotatedName("shot.png", "png")).toBe("shot.annotated.png");
    expect(annotatedName("noext", "webp")).toBe("noext.annotated.webp");
  });

  it("is idempotent, so a re-edit never grows the name — even across a codec change", () => {
    expect(annotatedName("shot.annotated.webp", "webp")).toBe("shot.annotated.webp");
    expect(annotatedName(annotatedName("a.b.gif", "webp"), "webp")).toBe("a.b.annotated.webp");
    expect(annotatedName("big.annotated.png", "webp")).toBe("big.annotated.webp");
  });
});

describe("the annotation render", () => {
  it("is one lossy WebP codec at a quality above a phone camera's own JPEG", () => {
    expect(ANNOTATION_CODEC.mimeType).toBe("image/webp");
    expect(ANNOTATION_CODEC.quality).toBeGreaterThanOrEqual(0.85);
    expect(ANNOTATION_CODEC.quality).toBeLessThan(1);
  });

  it("bounds the PIXEL COUNT, which is what the output size is a function of", () => {
    // A 400 KB 3000x2400 JPEG is 29 MB of pixels once decoded; rendering all
    // of them is the bloat, whatever the codec.
    expect(annotatedSize(4000, 3000)).toEqual({ width: 2048, height: 1536 });
    expect(annotatedSize(3000, 4000)).toEqual({ width: 1536, height: 2048 });
    expect(annotatedSize(2048, 100)).toEqual({ width: 2048, height: 100 });
  });

  it("never upscales, and keeps the aspect on the long edge", () => {
    expect(annotatedSize(1440, 900)).toEqual({ width: 1440, height: 900 });
    expect(annotatedSize(1, 1)).toEqual({ width: 1, height: 1 });
    const size = annotatedSize(4096, 1);
    expect(size.width).toBe(MAX_ANNOTATED_EDGE);
    expect(size.height).toBe(1);
  });

  it("names the file after the bytes the browser PRODUCED, never the codec asked for", () => {
    // `toDataURL` substitutes PNG silently where WebP cannot be encoded.
    expect(dataUrlMimeType("data:image/webp;base64,UklG")).toBe("image/webp");
    expect(dataUrlMimeType("data:image/png;base64,iVBO")).toBe("image/png");
    expect(annotatedExtension("image/webp")).toBe("webp");
    expect(annotatedExtension("image/png")).toBe("png");
    expect(annotatedExtension(dataUrlMimeType("garbage"))).toBe("png");
  });
});

describe("fileFromStaged", () => {
  it("rebuilds the picked File from a staged row, bytes and name intact", async () => {
    const bytes = Uint8Array.from([0x89, 0x50, 0x4e, 0x47, 0, 255]);
    const file = fileFromStaged({
      name: "shot.png",
      size: bytes.length,
      content_base64: base64FromBytes(bytes),
    });
    expect(file.name).toBe("shot.png");
    expect(file.type).toBe("image/png");
    expect([...new Uint8Array(await file.arrayBuffer())]).toEqual([...bytes]);
  });

  it("hands back the very File a row was staged from, so identity-keyed state survives", () => {
    const picked = new File([Uint8Array.of(1)], "shot.png", { type: "image/png" });
    const rebuilt = fileFromStaged({ name: "shot.png", size: 1, content_base64: "AQ==", file: picked });
    expect(rebuilt).toBe(picked);
  });
});
