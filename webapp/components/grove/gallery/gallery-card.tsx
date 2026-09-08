"use client";

import Link from "next/link";
import { useEffect, useMemo } from "react";
import {
  DownloadIcon,
  HistoryIcon,
  ImageIcon,
  Maximize2Icon,
  MoreVerticalIcon,
  TreesIcon,
} from "lucide-react";

import { CardShell } from "@/components/grove/card";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { galleryCardCaption, galleryCardTitle, galleryOpenTarget } from "@/lib/grove/adapters";
import type { GalleryItemView } from "@/lib/grove/api";
import { useGalleryPreview } from "@/lib/grove/hooks";

/**
 * One diagram: the first page as a sheet on a muted field, then two lines —
 * the session (or workspace, or id) that produced it with a menu, and
 * `N pages • Project`. Every verb lives in the menu; the preview itself opens
 * the viewer, so the card face carries nothing but the picture and its name.
 *
 * The last menu item follows liveness (`galleryOpenTarget`): a live workspace
 * is where the agent still is, a dead one's transcript is what the session
 * page reads.
 */
export function GalleryCard({
  item,
  onView,
  onDownload,
  onExport,
  onNeedsPreview,
}: {
  item: GalleryItemView;
  onView: (item: GalleryItemView) => void;
  onDownload: (item: GalleryItemView) => void;
  onExport: (item: GalleryItemView) => void;
  /** Called once when the daemon has no picture for this content; the page renders one. */
  onNeedsPreview: (item: GalleryItemView) => void;
}): React.ReactNode {
  const preview = useGalleryPreview(item.id, item.digest, item.preview_ready);
  const title = galleryCardTitle(item);
  const caption = galleryCardCaption(item);
  const open = galleryOpenTarget(item);
  const src = useMemo(
    () => (preview.data ? `data:image/png;base64,${preview.data.content_base64}` : null),
    [preview.data],
  );

  useEffect(() => {
    if (!item.preview_ready) onNeedsPreview(item);
  }, [item, onNeedsPreview]);

  return (
    <CardShell data-testid="gallery-card" data-item-id={item.id}>
      <button
        type="button"
        onClick={() => onView(item)}
        // The well centres whatever it holds — the placeholder and the error
        // both sit in the middle — and only the IMAGE anchors itself to the
        // top (`self-start`). Anchoring the well itself put "Rendering
        // preview…" in the top corner of every card while the renderer worked.
        className="surface-sunken flex aspect-[4/3] w-full cursor-pointer items-center justify-center overflow-hidden text-start outline-none focus-visible:ring-2 focus-visible:ring-ring"
        aria-label={`View ${title}`}
        data-testid="gallery-card-preview"
      >
        {src ? (
          // A data URL the daemon vouched for as a PNG; `next/image` has no
          // business optimising a picture that never leaves the browser.
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={src}
            alt={`First page of ${item.name}`}
            // Fill the well's WIDTH and let the bottom clip, like a document
            // scan: a diagram is read from its top, so the part that survives
            // a clip is the part that identifies it, and the margin around a
            // letterboxed sheet was the emptiest thing on the page. Never
            // `object-cover`, which would crop the SIDES of a wide diagram.
            className="surface-raised w-full self-start"
            draggable={false}
          />
        ) : item.preview_ready && preview.isPending ? (
          <Skeleton className="h-full w-full" />
        ) : (
          <span className="flex flex-col items-center gap-1 text-content-tertiary text-xs">
            <ImageIcon aria-hidden className="size-4" />
            {preview.isError ? "Preview unavailable" : "Rendering preview…"}
          </span>
        )}
      </button>
      {/* The same `surface-header` band every SectionCard title wears, so a
          gallery card's caption reads as a title bar for the picture above it
          rather than a footer under it. */}
      <div className="surface-header flex min-w-0 items-start gap-1 border-t border-border px-3 pt-2 pb-2.5">
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="min-w-0 truncate text-sm font-medium" title={`${title} — ${item.relative_path}`}>
            {title}
          </span>
          <span className="min-w-0 truncate text-content-tertiary text-xs tabular-nums" data-testid="gallery-card-caption">
            {caption}
          </span>
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="size-7 min-h-[24px] min-w-[24px] shrink-0"
              aria-label={`Actions for ${title}`}
              data-testid="gallery-card-menu"
            >
              <MoreVerticalIcon aria-hidden />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            <DropdownMenuLabel className="truncate font-mono text-xs font-normal text-content-tertiary" title={item.relative_path}>
              {item.relative_path}
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => onView(item)}>
              <Maximize2Icon aria-hidden />
              View
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => onDownload(item)}>
              <DownloadIcon aria-hidden />
              Download .drawio
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => onExport(item)}>
              <ImageIcon aria-hidden />
              Export PNG
            </DropdownMenuItem>
            {open ? (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuItem asChild>
                  <Link href={open.href} data-testid={`gallery-open-${open.kind}`}>
                    {open.kind === "workspace" ? <TreesIcon aria-hidden /> : <HistoryIcon aria-hidden />}
                    {open.kind === "workspace" ? "Open workspace" : "Open session"}
                  </Link>
                </DropdownMenuItem>
              </>
            ) : null}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </CardShell>
  );
}
