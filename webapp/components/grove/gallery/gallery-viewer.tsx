"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { DownloadIcon, ImageIcon } from "lucide-react";

import { ErrorState } from "@/components/elements/error-state";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  galleryCardCaption,
  galleryCardTitle,
  galleryExportFilename,
  galleryPageDocument,
  galleryPages,
} from "@/lib/grove/adapters";
import type { GalleryItemView } from "@/lib/grove/api";
import { useGalleryDocument } from "@/lib/grove/hooks";

import { useDrawioFrame } from "./drawio-frame";

/**
 * The lightbox: one diagram in draw.io's own chromeless viewer, with
 * Download and Export PNG in the header. Read-only by construction — the
 * frame is mounted with `chrome=0` and `autosave` off, and the daemon has no
 * write route for a gallery item anyway.
 *
 * The page tabs are Grove's, not the editor's: the chromeless embed draws
 * none, and the protocol's `load` takes no page selector, so switching a
 * page hands the frame a one-page document (`galleryPageDocument`). Export
 * therefore always answers for the page on screen, and its filename carries
 * the page name when the file has more than one.
 */
export function GalleryViewer({
  item,
  base,
  onClose,
  onDownload,
  onExport,
}: {
  item: GalleryItemView | null;
  base: string | null;
  onClose: () => void;
  onDownload: (item: GalleryItemView) => void;
  /** Hand back the frame's export so one save path serves the card menu and the viewer. */
  onExport: (
    item: GalleryItemView,
    exportPng: (scale: number) => Promise<string | null>,
    filename: string,
  ) => void;
}): React.ReactNode {
  const document = useGalleryDocument(item?.id ?? null);
  const frame = useDrawioFrame(base);
  const [frameElement, setFrameElement] = useState<HTMLIFrameElement | null>(null);
  // Memoized, and that is load-bearing: an inline callback ref is a new
  // function every render, so React detaches (null) and re-attaches it on
  // each one — and `frame.ref(null)` resets the frame's readiness, which
  // made the viewer un-ready itself the moment `load` set it ready.
  const attachFrame = useCallback(
    (element: HTMLIFrameElement | null) => {
      frame.ref(element);
      setFrameElement(element);
    },
    [frame.ref],
  );
  const [exporting, setExporting] = useState(false);
  const pages = useMemo(() => (document.data ? galleryPages(document.data.xml) : []), [document.data]);
  const [pageId, setPageId] = useState<string | null>(null);
  const current = pages.find((page) => page.id === pageId) ?? pages[0] ?? null;

  // A new document starts on its first page; the reader's page choice does
  // not carry from one diagram to the next.
  useEffect(() => {
    setPageId(null);
  }, [item?.id]);

  useEffect(() => {
    if (!document.data || current === null) return;
    const xml =
      pages.length > 1 ? galleryPageDocument(document.data.xml, current.id) : document.data.xml;
    if (xml !== null) void frame.load(xml).then(() => frame.fit());
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the DOCUMENT and the PAGE are the triggers; the frame object is stable per mount
  }, [document.data, current?.id]);

  // Fit once the editor has laid the page out AND the frame has its final
  // size: the dialog animates open and the page tabs land after the first
  // load, so a fit fired on `load` alone measures a viewport that then grows.
  // Same ResizeObserver idiom as the workspace Diagram tab.
  useEffect(() => {
    const element = frameElement;
    if (!frame.ready || !element) return;
    let timer: number | undefined;
    const schedule = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => frame.fit(), 150);
    };
    const observer = new ResizeObserver(schedule);
    observer.observe(element);
    schedule();
    return () => {
      observer.disconnect();
      window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- readiness and the element are the triggers
  }, [frame.ready, frameElement]);

  const title = item ? galleryCardTitle(item) : "";
  const filename = item
    ? galleryExportFilename(item.name, pages.length > 1 ? current?.name : undefined)
    : "diagram.png";

  return (
    <Dialog open={item !== null} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent
        className="flex h-[92dvh] w-[96vw] max-w-[96vw] flex-col gap-3 p-4 sm:max-w-[96vw]"
        data-testid="gallery-viewer"
      >
        <DialogHeader className="flex-row items-start gap-3 pr-8 text-start">
          <div className="min-w-0 flex-1">
            <DialogTitle className="truncate text-base">{title}</DialogTitle>
            <DialogDescription className="truncate font-mono text-xs">
              {item ? `${item.relative_path}  •  ${galleryCardCaption(item)}` : ""}
            </DialogDescription>
          </div>
          {item ? (
            <div className="flex shrink-0 items-center gap-1">
              <Button variant="outline" size="sm" onClick={() => onDownload(item)} data-testid="gallery-viewer-download">
                <DownloadIcon aria-hidden />
                Download
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={!frame.ready || exporting}
                onClick={() => {
                  setExporting(true);
                  onExport(
                    item,
                    (scale) => frame.exportPng({ scale }).finally(() => setExporting(false)),
                    filename,
                  );
                }}
                data-testid="gallery-viewer-export"
              >
                <ImageIcon aria-hidden />
                Export PNG
              </Button>
            </div>
          ) : null}
        </DialogHeader>
        {pages.length > 1 && current ? (
          <Tabs value={current.id} onValueChange={setPageId} className="shrink-0">
            <TabsList variant="line" className="h-8 max-w-full overflow-x-auto" aria-label="Pages">
              {pages.map((page) => (
                <TabsTrigger key={page.id} value={page.id} className="flex-none text-xs" data-testid="gallery-viewer-page">
                  {page.name}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        ) : null}
        <div className="relative min-h-0 flex-1 overflow-hidden border border-border">
          {base === null ? (
            <ErrorState
              title="No diagram viewer is configured"
              detail="NEXT_PUBLIC_GROVE_DRAWIO_URL is set to a value this app will not load."
              retrying={false}
              onRetry={onClose}
            />
          ) : document.isError ? (
            <ErrorState
              title="Couldn't load this diagram"
              detail={document.error.message}
              retrying={document.isFetching}
              onRetry={() => void document.refetch()}
            />
          ) : (
            <>
              {!frame.ready ? (
                <Skeleton className="absolute inset-0" aria-hidden />
              ) : null}
              {item ? (
                <iframe
                  key={item.id}
                  ref={attachFrame}
                  src={frame.src}
                  title={`Diagram ${item.name}`}
                  className="h-full w-full border-0"
                  data-testid="gallery-viewer-frame"
                />
              ) : null}
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
