"use client";

import { useState } from "react";
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
import {
  galleryCardCaption,
  galleryCardTitle,
  galleryExportFilename,
} from "@/lib/grove/adapters";
import type { GalleryItemView } from "@/lib/grove/api";
import { useGalleryDocument } from "@/lib/grove/hooks";

import { DiagramFrame, DiagramPageTabs, useDiagramStage } from "./diagram-stage";

/**
 * The lightbox: one diagram in draw.io's own chromeless viewer, with
 * Download and Export PNG in the header. Read-only by construction — the
 * frame is mounted with `chrome=0` and `autosave` off, and the daemon has no
 * write route for a gallery item anyway.
 *
 * The frame, the page tabs and the fit are `useDiagramStage`, shared with the
 * workspace artifact viewer. Export always answers for the page on screen,
 * and its filename carries the page name when the file has more than one.
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
  const stage = useDiagramStage(base, document.data?.xml, item?.id ?? null);
  const { frame, pages, current } = stage;
  const [exporting, setExporting] = useState(false);

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
        <DiagramPageTabs stage={stage} testId="gallery-viewer-page" />
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
            item && (
              <DiagramFrame
                stage={stage}
                frameKey={item.id}
                title={`Diagram ${item.name}`}
                testId="gallery-viewer-frame"
              />
            )
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
