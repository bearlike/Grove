"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  configureMessage,
  drawioOrigin,
  fitMessage,
  galleryExportMessage,
  galleryExportToken,
  galleryViewerUrl,
  loadMessage,
  parseDrawioEvent,
  pngBase64,
} from "@/lib/grove/adapters";

/** How long a load or an export gets before the caller is told it never came. */
const FRAME_TIMEOUT_MS = 20_000;
/**
 * How long a SECOND `load` waits for the editor's `load` event before assuming
 * the document landed anyway. Measured against the hosted editor: the event
 * is emitted for the first document a frame receives and not reliably for a
 * later one, while the later document still renders and exports correctly.
 */
const RELOAD_SETTLE_MS = 800;

export interface DrawioFrame {
  /** Attach to the `<iframe>`; the `src` is the chromeless viewer for `base`. */
  readonly ref: (element: HTMLIFrameElement | null) => void;
  readonly src: string;
  /** The editor has said `load` for the document last handed to it. */
  readonly ready: boolean;
  /** Hand the frame a document; resolves once the editor has laid it out. */
  readonly load: (xml: string) => Promise<void>;
  /** Ask for a PNG of one page at `scale`; resolves to base64 PNG bytes, or `null` on timeout. */
  readonly exportPng: (options: { pageId?: string; scale: number }) => Promise<string | null>;
  readonly fit: () => void;
}

/**
 * One draw.io viewer frame, driven through the embed protocol.
 *
 * The gallery has two consumers with opposite visibility — the lightbox the
 * reader looks at, and a hidden renderer that produces previews for cards
 * the daemon has none for — and both need the same three verbs: load,
 * export, fit. `DiagramTab` owns a fourth concern (the collaboration state
 * machine) that neither needs, which is why this is a sibling hook over the
 * same pure adapter rather than a prop on that tab.
 *
 * Both origin AND source are checked on every inbound message: the origin
 * proves the host, the source proves it is THIS frame and not the workspace
 * tab's editor on the same page.
 */
export function useDrawioFrame(base: string | null): DrawioFrame {
  const frame = useRef<HTMLIFrameElement | null>(null);
  const [ready, setReady] = useState(false);
  const pendingLoad = useRef<{ resolve: () => void; timer: number } | null>(null);
  const exports = useRef(new Map<string, { resolve: (png: string | null) => void; timer: number }>());
  /** The document to hand over on `init`, when a load was asked for before the frame was up. */
  const queued = useRef<string | null>(null);
  const origin = base ? drawioOrigin(base) : null;
  const src = base ? galleryViewerUrl(base) : "about:blank";

  const post = useCallback(
    (message: string) => {
      if (origin) frame.current?.contentWindow?.postMessage(message, origin);
    },
    [origin],
  );

  const ref = useCallback((element: HTMLIFrameElement | null) => {
    frame.current = element;
    if (element === null) {
      loadedOnce.current = false;
      initialized.current = false;
      setReady(false);
    }
  }, []);

  useEffect(() => {
    if (!origin) return;
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== origin) return;
      if (event.source !== frame.current?.contentWindow) return;
      const parsed = parseDrawioEvent(event.data);
      if (parsed === null) return;
      if (parsed.kind === "configure") return post(configureMessage());
      if (parsed.kind === "init") {
        initialized.current = true;
        if (queued.current !== null) post(loadMessage(queued.current, "read_only"));
        return;
      }
      if (parsed.kind === "load") {
        loadedOnce.current = true;
        setReady(true);
        const waiting = pendingLoad.current;
        pendingLoad.current = null;
        if (waiting) {
          window.clearTimeout(waiting.timer);
          waiting.resolve();
        }
        return;
      }
      if (parsed.kind === "export") {
        const token = galleryExportToken(parsed.message);
        const waiting = token === null ? undefined : exports.current.get(token);
        if (!waiting || token === null) return;
        exports.current.delete(token);
        window.clearTimeout(waiting.timer);
        waiting.resolve(pngBase64(parsed.data));
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [origin, post]);

  const loadedOnce = useRef(false);
  /** The editor has said `init`; before that a `load` is queued, never posted. */
  const initialized = useRef(false);

  const load = useCallback(
    (xml: string): Promise<void> =>
      new Promise((resolve) => {
        queued.current = xml;
        const previous = pendingLoad.current;
        if (previous) {
          window.clearTimeout(previous.timer);
          previous.resolve();
        }
        // Readiness is sticky: the first document's `load` is what proves the
        // frame is up, and a later document does not take that away.
        const wait = loadedOnce.current ? RELOAD_SETTLE_MS : FRAME_TIMEOUT_MS;
        const timer = window.setTimeout(() => {
          if (pendingLoad.current?.timer === timer) pendingLoad.current = null;
          resolve();
        }, wait);
        pendingLoad.current = { resolve, timer };
        // A frame that has already said `init` takes the document at once; one
        // that has not gets it on `init` from the queue. Posting EARLIER is not
        // harmless: a `load` that lands while the editor is still booting is
        // consumed silently and the `load` event it would have answered with
        // never comes, so the frame reads as never ready.
        if (initialized.current) post(loadMessage(xml, "read_only"));
      }),
    [post],
  );

  const exportPng = useCallback(
    (options: { pageId?: string; scale: number }): Promise<string | null> =>
      new Promise((resolve) => {
        const token = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
        const timer = window.setTimeout(() => {
          exports.current.delete(token);
          resolve(null);
        }, FRAME_TIMEOUT_MS);
        exports.current.set(token, { resolve, timer });
        post(galleryExportMessage(token, options));
      }),
    [post],
  );

  const fit = useCallback(() => post(fitMessage()), [post]);

  return { ref, src, ready, load, exportPng, fit };
}
