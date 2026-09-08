import { describe, expect, it } from "vitest";

import {
  configureMessage,
  DEFAULT_DRAWIO_URL,
  draftFilename,
  drawioEmbedUrl,
  drawioOrigin,
  firstPageId,
  fitMessage,
  flushMessages,
  flushToken,
  loadMessage,
  parseDrawioEvent,
  pngBase64,
  previewMessage,
  previewToken,
  sanitizeDrawioBase,
  savedStatusMessage,
} from "@/lib/grove/adapters";

/**
 * The editor base reaches an `iframe src` AND a `postMessage` target origin, so
 * these cases are a security boundary rather than input tidying: a value that
 * gets through decides who receives the document.
 */
describe("sanitizeDrawioBase", () => {
  it("accepts an https host", () => {
    expect(sanitizeDrawioBase("https://embed.diagrams.net/")).toBe(
      "https://embed.diagrams.net/",
    );
  });

  it("refuses a non-http scheme", () => {
    for (const value of [
      "javascript:alert(1)",
      "data:text/html,<b>",
      "file:///etc/passwd",
    ]) {
      expect(sanitizeDrawioBase(value)).toBeNull();
    }
  });

  it("refuses credentials in the URL", () => {
    expect(sanitizeDrawioBase("https://user:secret@draw.example/")).toBeNull();
  });

  it("refuses plain http off loopback but allows it on it", () => {
    expect(sanitizeDrawioBase("http://draw.example/")).toBeNull();
    expect(sanitizeDrawioBase("http://localhost:8080/")).toBe(
      "http://localhost:8080/",
    );
    expect(sanitizeDrawioBase("http://127.0.0.1:8080/")).toBe(
      "http://127.0.0.1:8080/",
    );
  });

  it("discards a caller's query and fragment rather than merging them", () => {
    // A base contributing its own parameters could switch off `proto=json` and
    // silently change the protocol under the adapter.
    expect(sanitizeDrawioBase("https://draw.example/?proto=xml#frag")).toBe(
      "https://draw.example/",
    );
  });

  it("treats empty and unparseable values as unconfigured", () => {
    expect(sanitizeDrawioBase("   ")).toBeNull();
    expect(sanitizeDrawioBase(undefined)).toBeNull();
    expect(sanitizeDrawioBase("not a url")).toBeNull();
  });
});

describe("drawioEmbedUrl", () => {
  it("asks for the JSON protocol and the configure round trip", () => {
    const url = drawioEmbedUrl(DEFAULT_DRAWIO_URL, "active");
    expect(url).toContain("proto=json");
    expect(url).toContain("configure=1");
    expect(url).toContain("embed=1");
  });

  it("opens the editor with the shapes panel hidden, so the diagram gets the width", () => {
    expect(drawioEmbedUrl(DEFAULT_DRAWIO_URL, "active")).toContain("sidebar=0");
  });

  it("mounts a read-only diagram with no chrome and no save button", () => {
    const url = drawioEmbedUrl(DEFAULT_DRAWIO_URL, "read_only");
    expect(url).toContain("chrome=0");
    expect(url).toContain("noSaveBtn=1");
  });

  it("never carries document bytes, a path or a token", () => {
    // The URL is readable in the DOM, in a referrer and in a third party's
    // logs, so the document only ever crosses as a postMessage payload.
    const url = drawioEmbedUrl(DEFAULT_DRAWIO_URL, "active");
    expect(url).not.toContain("mxfile");
    expect(url).not.toContain("xml=");
    expect(url).not.toContain("token");
  });

  it("resolves the exact origin the frame must be addressed at", () => {
    expect(drawioOrigin("https://embed.diagrams.net/")).toBe(
      "https://embed.diagrams.net",
    );
  });
});

describe("parseDrawioEvent", () => {
  it("reads the handshake events", () => {
    expect(parseDrawioEvent(JSON.stringify({ event: "configure" }))).toEqual({
      kind: "configure",
    });
    expect(parseDrawioEvent(JSON.stringify({ event: "init" }))).toEqual({
      kind: "init",
    });
    expect(parseDrawioEvent(JSON.stringify({ event: "load" }))).toEqual({
      kind: "load",
    });
  });

  it("reads autosave and save as the same document-bearing event", () => {
    expect(
      parseDrawioEvent(JSON.stringify({ event: "autosave", xml: "<mxfile/>" })),
    ).toEqual({
      kind: "save",
      xml: "<mxfile/>",
      autosave: true,
    });
    expect(
      parseDrawioEvent(JSON.stringify({ event: "save", xml: "<mxfile/>" })),
    ).toEqual({
      kind: "save",
      xml: "<mxfile/>",
      autosave: false,
    });
  });

  it("reads XML and PNG export answers through one correlated event", () => {
    expect(
      parseDrawioEvent(
        JSON.stringify({
          event: "export",
          xml: "<mxfile/>",
          message: { action: "export" },
        }),
      ),
    ).toMatchObject({ kind: "export", data: "<mxfile/>" });
    expect(
      parseDrawioEvent(
        JSON.stringify({
          event: "export",
          data: "data:image/png;base64,UE5H",
          message: { action: "export" },
        }),
      ),
    ).toMatchObject({ kind: "export", data: "data:image/png;base64,UE5H" });
  });

  it("reads the event's OWN xml, never the echoed request", () => {
    // An autosave frame echoes the original `load` request on `message`,
    // including the XML we sent — so reading `message.xml` would persist the
    // document as it was when the editor opened and silently discard every
    // edit since. The two are made to disagree here on purpose.
    const parsed = parseDrawioEvent(
      JSON.stringify({
        event: "autosave",
        xml: "<edited/>",
        message: { action: "load", xml: "<as-loaded/>" },
      }),
    );
    expect(parsed).toEqual({ kind: "save", xml: "<edited/>", autosave: true });
  });

  it("discards anything that is not a draw.io JSON frame", () => {
    // A stray postMessage from an extension must not reach a branch.
    expect(parseDrawioEvent("ready")).toBeNull();
    expect(parseDrawioEvent(undefined)).toBeNull();
    expect(parseDrawioEvent({ event: "save", xml: "<mxfile/>" })).toBeNull();
    expect(parseDrawioEvent("{not json")).toBeNull();
    expect(parseDrawioEvent(JSON.stringify({ nope: 1 }))).toBeNull();
  });

  it("keeps a document-less save out of the save branch", () => {
    expect(parseDrawioEvent(JSON.stringify({ event: "save" }))).toEqual({
      kind: "other",
      event: "save",
    });
  });
});

describe("outbound messages", () => {
  it("configures uncompressed XML, which is what keeps the file diffable", () => {
    expect(JSON.parse(configureMessage())).toEqual({
      action: "configure",
      config: { compressXml: false },
    });
  });

  it("loads an editable document with autosave on and no unsaved claim", () => {
    expect(JSON.parse(loadMessage("<mxfile/>", "active"))).toEqual({
      action: "load",
      xml: "<mxfile/>",
      autosave: 1,
      modified: 0,
    });
  });

  it("loads a read-only document with autosave OFF", () => {
    // A viewer that autosaved would emit saves the daemon refuses, presenting
    // as an error the reader cannot act on.
    expect(JSON.parse(loadMessage("<mxfile/>", "read_only")).autosave).toBe(0);
  });

  it("says Saved only as an explicit, unmodified status", () => {
    expect(JSON.parse(savedStatusMessage())).toEqual({
      action: "status",
      message: "Saved",
      modified: false,
    });
  });

  it("commits the caret BEFORE exporting, in that order", () => {
    // `resetEditor` calls the graph's stopEditing; without it an export answers
    // with the label as it was before the caret entered the cell, so the order
    // is the whole correctness of the flush.
    const [first, second] = flushMessages("t1").map(
      (m) => JSON.parse(m) as { action: string },
    );
    expect(first.action).toBe("resetEditor");
    expect(second.action).toBe("export");
    expect(JSON.parse(flushMessages("t1")[1]).format).toBe("xml");
  });

  it("correlates a flush so two outstanding ones cannot resolve each other", () => {
    // A liveness probe and a stop can both be in flight and they mean opposite
    // things: resolving the wrong one either adopts over unsaved work or stops
    // without the reader's last edit.
    const request = JSON.parse(flushMessages("probe-7")[1]) as unknown;
    expect(flushToken(request)).toBe("probe-7");
    expect(flushToken({ action: "export", format: "xml" })).toBeNull();
    expect(flushToken(null)).toBeNull();
    expect(flushToken("export")).toBeNull();
  });
});

it("fits the viewport without reloading or mutating the document", () => {
  expect(JSON.parse(fitMessage())).toEqual({ action: "fit", border: 16, maxScale: 1 });
});

describe("revision-bound first-page preview", () => {
  it("exports the first page the editor is showing, with an echoed identity and NO xml", () => {
    const xml = '<mxfile><diagram id="first"/><diagram id="second"/></mxfile>';
    const message = JSON.parse(
      previewMessage(firstPageId(xml) ?? "", "session-1", "revision-1"),
    );
    expect(message).toMatchObject({
      action: "export",
      format: "png",
      pageId: "first",
      grovePreview: { session_id: "session-1", revision: "revision-1" },
    });
    // An `xml` on an export request makes draw.io `setFileData` the document
    // again: pages rebuilt, viewport thrown to the stored position, a label
    // typed since the acknowledged revision overwritten. Never send one.
    expect(message).not.toHaveProperty("xml");
    // Real draw.io echoes the WHOLE request, not request.message. Exercise the
    // receive parser too: matching invented halves previously hid lost exports.
    const response = parseDrawioEvent(JSON.stringify({
      event: "export", format: "png", data: "data:image/png;base64,UE5H", message,
    }));
    expect(response?.kind).toBe("export");
    if (response?.kind !== "export") throw new Error("missing export response");
    expect(previewToken(response.message)).toEqual({
      sessionId: "session-1",
      revision: "revision-1",
    });
    expect(pngBase64(response.data)).toBe("UE5H");
    expect(message).not.toHaveProperty("message");
  });

  it("accepts a PNG data URL only and preserves retry-able failures as null", () => {
    expect(pngBase64("data:image/png;base64,UE5H")).toBe("UE5H");
    expect(pngBase64("data:image/jpeg;base64,UE5H")).toBeNull();
    expect(pngBase64("data:image/png;base64,not valid")).toBeNull();
    expect(
      firstPageId("<mxfile><diagram name='only' id='page-1'/></mxfile>"),
    ).toBe("page-1");
    expect(firstPageId("<mxfile/>")).toBeNull();
  });
});

describe("draftFilename", () => {
  it("keeps only the basename, so a recovered draft opens anywhere", () => {
    expect(draftFilename("docs/design/flow.drawio")).toBe("flow.drawio");
    expect(draftFilename("flow.drawio")).toBe("flow.drawio");
  });
});
