/**
 * The draw.io embed protocol, as pure functions.
 *
 * Everything here is a plain function of its arguments — no `window`, no
 * iframe, no clock — so the whole protocol is exercisable without a browser.
 * The stateful half (which save is in flight, what the last acknowledged
 * revision was) lives in `../runtime/diagram.ts`.
 *
 * Verified against the real hosted editor: `configure` → `init` → `load`, and
 * an edit emits `autosave` carrying uncompressed `<mxfile>` XML once
 * `compressXml:false` is configured.
 */

/**
 * The official hosted embed. An operator may point this at their own
 * compatible host, which is the honest answer to "this loads third-party
 * JavaScript" — see `NEXT_PUBLIC_GROVE_DRAWIO_URL`.
 */
export const DEFAULT_DRAWIO_URL = "https://embed.diagrams.net/";

/**
 * The query the editor is launched with.
 *
 * `proto=json` is what makes every exchange below a typed message instead of
 * the legacy string protocol; `configure=1` is what buys the round trip where
 * `compressXml:false` is set, without which every save would arrive as a
 * deflated blob Grove would have to inflate before it could be a `.drawio`
 * file anyone else can read.
 */
const EDITOR_QUERY = [
  "embed=1",
  "proto=json",
  "configure=1",
  "spin=1",
  "libraries=1",
  "noExitBtn=1",
  "saveAndExit=0",
  // The shapes panel starts hidden: inside a work tab it took a third of the
  // width the diagram was meant to fill, and `fit` then centred the drawing in
  // what was left. View ▸ Shapes (Ctrl+Shift+K) still opens it on demand.
  "sidebar=0",
];

/** The read-only viewer: no chrome, no save button, nothing editable. */
const VIEWER_QUERY = [
  "embed=1",
  "proto=json",
  "configure=1",
  "spin=1",
  "chrome=0",
  "noSaveBtn=1",
];

/**
 * A deployment's editor base, or `null` when the configured value is unusable.
 *
 * The value is operator configuration, but it still reaches an `iframe src` and
 * a `postMessage` target origin, so it is sanitized rather than trusted:
 *
 * - HTTP(S) only — no `javascript:`, no `data:`, no `file:`.
 * - No credentials in the URL, which would otherwise be sent to a third party
 *   and shown in the DOM.
 * - The caller's query and fragment are DISCARDED, not merged. Grove owns every
 *   embed parameter; a base that could contribute its own could switch off
 *   `proto=json` and silently change the protocol under the adapter.
 * - Plain HTTP is accepted only for loopback. Anywhere else, an unencrypted
 *   editor would carry the document in clear text.
 *
 * `null` rather than a fallback to the default: silently loading a *different*
 * host than the operator configured is worse than refusing, because nothing
 * would ever say the setting was ignored.
 */
export function sanitizeDrawioBase(
  raw: string | undefined | null,
): string | null {
  const trimmed = raw?.trim();
  if (!trimmed) return null;
  let url: URL;
  try {
    url = new URL(trimmed);
  } catch {
    return null;
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") return null;
  if (url.username || url.password) return null;
  if (url.protocol === "http:" && !isLoopback(url.hostname)) return null;
  url.search = "";
  url.hash = "";
  return url.toString();
}

function isLoopback(hostname: string): boolean {
  return (
    hostname === "localhost" ||
    hostname === "127.0.0.1" ||
    hostname === "[::1]" ||
    hostname === "::1"
  );
}

/**
 * The `src` for one editor mount.
 *
 * NO DOCUMENT XML, NO GROVE TOKEN, NO HOST PATH is ever placed here: a URL is
 * readable in the DOM, in a referrer and in a third party's logs. The document
 * only ever crosses as a `postMessage` payload to an exact origin.
 */
export function drawioEmbedUrl(
  base: string,
  mode: "active" | "read_only",
): string {
  const query = mode === "read_only" ? VIEWER_QUERY : EDITOR_QUERY;
  return `${base}${base.includes("?") ? "&" : "?"}${query.join("&")}`;
}

/** The exact origin to send to and to accept from. `null` for an unparseable base. */
export function drawioOrigin(base: string): string | null {
  try {
    return new URL(base).origin;
  } catch {
    return null;
  }
}

/** Every inbound editor event this app acts on, plus a catch-all. */
export type DrawioEvent =
  | { kind: "configure" }
  | { kind: "init" }
  | { kind: "load" }
  /** An edit the editor committed. Carries the full document. */
  | { kind: "save"; xml: string; autosave: boolean }
  /** The answer to an explicit XML or PNG export request. */
  | { kind: "export"; data: string; message: unknown }
  | { kind: "other"; event: string };

/**
 * One `MessageEvent.data` value, typed.
 *
 * Returns `null` for anything that is not a draw.io JSON frame at all, which
 * is how a stray `postMessage` from an extension on the same origin is
 * discarded without a branch at the call site. ORIGIN AND SOURCE ARE CHECKED BY
 * THE CALLER — this function cannot see them, and a shape check is not an
 * authenticity check.
 */
export function parseDrawioEvent(data: unknown): DrawioEvent | null {
  if (typeof data !== "string" || !data.startsWith("{")) return null;
  let frame: Record<string, unknown>;
  try {
    frame = JSON.parse(data) as Record<string, unknown>;
  } catch {
    return null;
  }
  const event = frame.event;
  if (typeof event !== "string") return null;
  switch (event) {
    case "configure":
      return { kind: "configure" };
    case "init":
      return { kind: "init" };
    case "load":
      return { kind: "load" };
    case "save":
    case "autosave":
      return typeof frame.xml === "string"
        ? { kind: "save", xml: frame.xml, autosave: event === "autosave" }
        : { kind: "other", event };
    case "export": {
      // XML exports use `xml`; image exports use a data URL in `data`.
      // Normalize them so the event router can correlate either one by message.
      const data = typeof frame.data === "string" ? frame.data : frame.xml;
      return typeof data === "string"
        ? { kind: "export", data, message: frame.message ?? null }
        : { kind: "other", event };
    }
    default:
      return { kind: "other", event };
  }
}

/**
 * `compressXml:false` is the whole reason the configure round trip is asked
 * for: it is what makes a saved page body readable XML rather than a deflated,
 * base64'd blob. A `.drawio` written any other way is still valid, but it stops
 * being a file a human or an agent can diff.
 */
export function configureMessage(): string {
  return JSON.stringify({
    action: "configure",
    config: { compressXml: false },
  });
}

/**
 * Hand the editor a document.
 *
 * `autosave` is `1` only for an editable mount. A viewer that autosaved would
 * emit saves the server is going to refuse anyway, and each refusal would
 * present to the reader as an error they cannot act on.
 *
 * `modified:0` states that what we just handed over IS the persisted version,
 * so the editor does not open already claiming unsaved changes.
 */
export function loadMessage(xml: string, mode: "active" | "read_only"): string {
  return JSON.stringify({
    action: "load",
    xml,
    autosave: mode === "read_only" ? 0 : 1,
    modified: 0,
  });
}

/**
 * The editor's own "Saved" indicator.
 *
 * Only ever sent once the daemon has ACKNOWLEDGED the latest draft. Sending it
 * on receipt of the save event would make the editor claim durability for bytes
 * that are still in flight — the one lie this whole surface exists to avoid.
 */
export function savedStatusMessage(): string {
  return JSON.stringify({
    action: "status",
    message: "Saved",
    modified: false,
  });
}

/** Fit the current diagram without reloading XML, clearing selection or saving. */
export function fitMessage(): string {
  return JSON.stringify({ action: "fit", border: 16, maxScale: 1 });
}

/** A visible, non-durable status — an unsaved draft, a conflict, a failure. */
export function unsavedStatusMessage(message: string): string {
  return JSON.stringify({ action: "status", message, modified: true });
}

/**
 * Commit whatever cell is being edited right now, then hand the document back.
 *
 * The two halves are one operation and must be sent in this order.
 * `resetEditor` calls the graph's `stopEditing`, which is what turns a label
 * being TYPED into part of the document; without it, `export` answers with the
 * text as it was before the caret entered the cell. This is why an absent
 * `autosave` event cannot be read as "nothing to save": an in-progress label
 * edit emits nothing at all until it commits.
 *
 * Sent on deliberate edges only — a UI stop, a reload the reader asked for —
 * never on a timer. `resetEditor` clears the selection and closes menus, so a
 * periodic flush would be a periodic interruption of the person drawing.
 */
export function flushMessages(token: string): readonly string[] {
  return [
    JSON.stringify({ action: "resetEditor" }),
    JSON.stringify({ action: "export", format: "xml", groveToken: token }),
  ];
}

/**
 * The token an `export` event answers for, or `null` for one we did not ask for.
 *
 * The editor echoes the whole request back on `message`, which is what lets two
 * overlapping flushes — a liveness probe and a stop — be told apart. Ordering
 * within one frame is guaranteed, so this is belt-and-braces rather than the
 * only mechanism; it matters because the two flushes have OPPOSITE meanings and
 * resolving the wrong one would either adopt over unsaved work or stop without
 * the reader's last edit.
 */
export function flushToken(message: unknown): string | null {
  if (typeof message !== "object" || message === null) return null;
  const token = (message as { groveToken?: unknown }).groveToken;
  return typeof token === "string" ? token : null;
}

/**
 * A first-page PNG export tied to the acknowledged document identity.
 *
 * NO `xml` on this request, ever. draw.io's export handler treats an inbound
 * `xml` as "render THIS instead" and does so by `setFileData` — a full document
 * reload that rebuilds the pages, throws the viewport to the file's stored
 * position (the diagram "scrolls to the bottom by itself" a moment after every
 * save) and would overwrite a label typed since the acknowledged revision.
 * Omitted, the editor renders what it is showing, which is the acknowledged
 * document on every edge that fires this (a fresh load, an adoption, an
 * acknowledged save) — the identity echoed back is what keeps a late image
 * from being filed against a newer revision.
 */
export function previewMessage(
  pageId: string,
  sessionId: string,
  revision: string,
): string {
  return JSON.stringify({
    action: "export",
    format: "png",
    pageId,
    // draw.io echoes this entire request as the export event's `message`.
    // Keep correlation on the request itself, just like XML flush tokens.
    grovePreview: { session_id: sessionId, revision },
  });
}

/** The revision identity echoed in a PNG export, never inferred from reply order. */
export function previewToken(
  message: unknown,
): { sessionId: string; revision: string } | null {
  if (typeof message !== "object" || message === null) return null;
  const preview = (message as { grovePreview?: unknown }).grovePreview;
  if (typeof preview !== "object" || preview === null) return null;
  const { session_id: sessionId, revision } = preview as {
    session_id?: unknown;
    revision?: unknown;
  };
  return typeof sessionId === "string" && typeof revision === "string"
    ? { sessionId, revision }
    : null;
}

/** The first mxfile page ID. Previews intentionally cover one page only. */
export function firstPageId(xml: string): string | null {
  // The document contract already validates mxfile XML. Match only the first
  // diagram start tag here to keep this adapter usable in the node test runtime.
  const page = /<diagram\b[^>]*\bid=(?:"([^"]*)"|'([^']*)')/i.exec(xml);
  return page?.[1] ?? page?.[2] ?? null;
}

/** Strip the PNG data URL only when it is exactly the image format this endpoint accepts. */
export function pngBase64(data: string): string | null {
  const match = /^data:image\/png;base64,([A-Za-z0-9+/]+={0,2})$/.exec(data);
  return match?.[1] ?? null;
}

/**
 * A `.drawio` filename, for a draft download.
 *
 * The path is worktree-relative, so its basename is the only part meaningful
 * outside the workspace; a separator that survived would make an unopenable
 * filename on every platform.
 */
export function draftFilename(path: string): string {
  const base = path.split("/").pop() ?? "diagram.drawio";
  return base.length > 0 ? base : "diagram.drawio";
}
