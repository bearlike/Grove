"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { galleryExportFilename, galleryPages } from "@/lib/grove/adapters";
import type { GalleryItemView } from "@/lib/grove/api";
import { groveClient, useSaveGalleryPreview } from "@/lib/grove/hooks";

import { useDrawioFrame } from "./drawio-frame";

/** Scale for a card thumbnail — a 4:3 well ~300px wide needs no more. */
const PREVIEW_SCALE = 1;
/** Scale for a saved export: crisp on a high-DPI screen, legible in a ticket. */
const EXPORT_SCALE = 2;

type Job =
  | { kind: "preview"; item: GalleryItemView }
  | { kind: "export"; item: GalleryItemView };

/**
 * One hidden draw.io frame that renders on the gallery's behalf.
 *
 * The daemon owns no draw.io renderer — the workspace tab has always let the
 * browser draw the picture and posted it back, and the gallery keeps that
 * arrangement. Cards whose content the daemon has never seen rendered are
 * queued here, drawn once, and posted under their content digest; every
 * later visit by anyone reads the cache. The same frame serves a card menu's
 * *Export PNG*, which has no viewer of its own open.
 *
 * Jobs run ONE AT A TIME: the frame holds one document, and an export answers
 * for whatever it is showing.
 */
export function useGalleryRenderer(base: string | null): {
  frame: React.ReactNode;
  requestPreview: (item: GalleryItemView) => void;
  requestExport: (item: GalleryItemView) => void;
  exporting: string | null;
} {
  const drawio = useDrawioFrame(base);
  const save = useSaveGalleryPreview();
  const queue = useRef<Job[]>([]);
  const queued = useRef(new Set<string>());
  const busy = useRef(false);
  const [exporting, setExporting] = useState<string | null>(null);
  const [, bump] = useState(0);

  const requestPreview = useCallback((item: GalleryItemView) => {
    const key = `preview:${item.digest}`;
    if (queued.current.has(key)) return;
    queued.current.add(key);
    queue.current.push({ kind: "preview", item });
    bump((n) => n + 1);
  }, []);

  const requestExport = useCallback((item: GalleryItemView) => {
    // An export jumps the queue: a person asked for it, the previews did not.
    queue.current.unshift({ kind: "export", item });
    bump((n) => n + 1);
  }, []);

  useEffect(() => {
    if (busy.current || base === null) return;
    const job = queue.current.shift();
    if (!job) return;
    busy.current = true;
    if (job.kind === "export") setExporting(job.item.id);
    void (async () => {
      try {
        const document = await groveClient.getGalleryDocument(job.item.id);
        await drawio.load(document.xml);
        if (job.kind === "preview") {
          const png = await drawio.exportPng({ scale: PREVIEW_SCALE });
          if (png !== null && document.digest === job.item.digest) {
            await save.mutateAsync({ id: job.item.id, digest: document.digest, contentBase64: png });
          }
        } else {
          const png = await drawio.exportPng({ scale: EXPORT_SCALE });
          if (png !== null) {
            const pages = galleryPages(document.xml);
            savePng(png, galleryExportFilename(job.item.name, pages.length > 1 ? pages[0]?.name : undefined));
          }
        }
      } catch {
        // A render that failed leaves the card saying "Rendering preview…"
        // until the next listing; nothing here is worth a toast per card.
      } finally {
        if (job.kind === "preview") queued.current.delete(`preview:${job.item.digest}`);
        else setExporting(null);
        busy.current = false;
        bump((n) => n + 1);
      }
    })();
  });

  const frame =
    base === null ? null : (
      // Off-screen rather than `display:none`: a hidden frame does not lay out,
      // and draw.io exports what it laid out. Kept the size of a card's well so
      // `fit` and the export agree on a viewport.
      <iframe
        ref={drawio.ref}
        src={drawio.src}
        title="Diagram renderer"
        aria-hidden
        tabIndex={-1}
        className="pointer-events-none fixed -left-[10000px] top-0 h-[600px] w-[800px] opacity-0"
        data-testid="gallery-renderer-frame"
      />
    );

  return { frame, requestPreview, requestExport, exporting };
}

/** Save base64 PNG bytes as a download — no upload, no daemon round trip. */
export function savePng(base64: string, filename: string): void {
  if (typeof window === "undefined") return;
  const bytes = Uint8Array.from(atob(base64), (char) => char.charCodeAt(0));
  const url = URL.createObjectURL(new Blob([bytes], { type: "image/png" }));
  const link = window.document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

/** Save a diagram's XML as its own `.drawio` — the bytes the daemon served, verbatim. */
export function saveDrawio(xml: string, filename: string): void {
  if (typeof window === "undefined") return;
  const url = URL.createObjectURL(new Blob([xml], { type: "application/xml" }));
  const link = window.document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}
