"use client";

import { useCallback, useMemo, useState } from "react";

import { ThreadListSearch } from "@/components/assistant-ui/thread-list";
import { EmptyState, EmptyStateGreeting } from "@/components/elements/empty-state";
import { ErrorState } from "@/components/elements/error-state";
import { CardShell } from "@/components/grove/card";
import { resolvedDrawioBase } from "@/components/grove/workspace/diagram-config";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  NO_GALLERY_FILTER,
  filterGallery,
  galleryCountLabel,
  galleryProjects,
  type GalleryFilter,
  type GallerySort,
} from "@/lib/grove/adapters";
import type { GalleryItemView } from "@/lib/grove/api";
import { groveClient, useGallery } from "@/lib/grove/hooks";

import { GalleryCard } from "./gallery-card";
import { saveDrawio, savePng, useGalleryRenderer } from "./gallery-renderer";
import { GalleryViewer } from "./gallery-viewer";

/** The sentinel the project `Select` uses for "every project" — Radix refuses an empty value. */
const ALL_PROJECTS = "__all__";

const SORT_LABEL: Record<GallerySort, string> = {
  newest: "Newest",
  name: "Name",
  project: "Project",
};

/**
 * The gallery page body: toolbar, grid, count, and the two things the grid
 * cannot draw for itself — the lightbox and the hidden renderer. All state is
 * visit-scoped; nothing here is persisted, because a filter on a browse
 * surface is a question asked once.
 */
export function Gallery(): React.ReactNode {
  const gallery = useGallery();
  const base = resolvedDrawioBase();
  const [filter, setFilter] = useState<GalleryFilter>(NO_GALLERY_FILTER);
  const [viewing, setViewing] = useState<GalleryItemView | null>(null);
  const renderer = useGalleryRenderer(base);

  const items = useMemo(() => gallery.data ?? [], [gallery.data]);
  const projects = useMemo(() => galleryProjects(items), [items]);
  const shown = useMemo(() => filterGallery(items, filter), [items, filter]);
  const settled = !gallery.isLoading && !gallery.error;

  const download = useCallback(async (item: GalleryItemView) => {
    const document = await groveClient.getGalleryDocument(item.id);
    saveDrawio(document.xml, item.name);
  }, []);

  const onNeedsPreview = renderer.requestPreview;
  const onExport = renderer.requestExport;

  return (
    <>
      <main className="flex min-h-0 min-w-0 flex-1 flex-col gap-3 p-4" data-testid="gallery-page">
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <div className="min-w-48 flex-1">
            <ThreadListSearch
              value={filter.query}
              onValueChange={(query) => setFilter({ ...filter, query })}
              placeholder="Search by session, project or file"
              aria-label="Search diagrams"
              className="h-9"
            />
          </div>
          <Select
            value={filter.project ?? ALL_PROJECTS}
            onValueChange={(value) =>
              setFilter({ ...filter, project: value === ALL_PROJECTS ? null : value })
            }
          >
            <SelectTrigger className="h-9 w-44" aria-label="Project" data-testid="gallery-project">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL_PROJECTS}>All projects</SelectItem>
              {projects.map((project) => (
                <SelectItem key={project.root} value={project.root}>
                  {project.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={filter.sort}
            onValueChange={(value) => setFilter({ ...filter, sort: value as GallerySort })}
          >
            <SelectTrigger className="h-9 w-36" aria-label="Sort" data-testid="gallery-sort">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {(Object.keys(SORT_LABEL) as GallerySort[]).map((sort) => (
                <SelectItem key={sort} value={sort}>
                  {SORT_LABEL[sort]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {gallery.isLoading ? <GallerySkeleton /> : null}
        {gallery.error ? (
          <ErrorState
            title="Couldn't load the gallery"
            detail={gallery.error.message}
            retrying={gallery.isFetching}
            onRetry={() => void gallery.refetch()}
          />
        ) : null}

        {/* Two empty states, the sessions page's rule: one is a fact about
            the host, the other a state the reader made and can undo. */}
        {settled && items.length === 0 ? (
          <EmptyState className="mx-auto my-auto" data-testid="gallery-empty">
            <EmptyStateGreeting>No diagrams yet</EmptyStateGreeting>
            <p className="text-content-tertiary text-sm">
              Open a .drawio in a workspace&apos;s Diagram tab and it appears here.
            </p>
          </EmptyState>
        ) : null}
        {settled && items.length > 0 && shown.length === 0 ? (
          <EmptyState className="mx-auto my-auto" data-testid="gallery-empty-filtered">
            <EmptyStateGreeting>No diagrams match</EmptyStateGreeting>
            <Button
              variant="outline"
              size="sm"
              className="mt-3"
              onClick={() => setFilter(NO_GALLERY_FILTER)}
              data-testid="gallery-clear-filters"
            >
              Clear filters and search
            </Button>
          </EmptyState>
        ) : null}

        {settled && shown.length > 0 ? (
          <>
            <div
              className="grid min-h-0 flex-1 auto-rows-min content-start gap-3 overflow-y-auto pr-1 grid-cols-[repeat(auto-fill,minmax(min(100%,15rem),1fr))]"
              data-testid="gallery-grid"
            >
              {shown.map((item) => (
                <GalleryCard
                  key={item.id}
                  item={item}
                  onView={setViewing}
                  onDownload={(target) => void download(target)}
                  onExport={onExport}
                  onNeedsPreview={onNeedsPreview}
                />
              ))}
            </div>
            <p className="text-content-tertiary shrink-0 text-xs tabular-nums" role="status" data-testid="gallery-count">
              {galleryCountLabel(shown.length, items.length)}
              {renderer.exporting ? "  •  exporting…" : ""}
            </p>
          </>
        ) : null}
      </main>
      <GalleryViewer
        item={viewing}
        base={base}
        onClose={() => setViewing(null)}
        onDownload={(item) => void download(item)}
        onExport={(_item, exportPng, filename) => {
          void exportPng(2).then((png) => {
            if (png !== null) savePng(png, filename);
          });
        }}
      />
      {renderer.frame}
    </>
  );
}

/** The page's own loading shape; `loading.tsx` reproduces it for the route transition. */
export function GallerySkeleton(): React.ReactNode {
  return (
    <div
      className="grid min-h-0 flex-1 auto-rows-min content-start gap-3 overflow-hidden grid-cols-[repeat(auto-fill,minmax(min(100%,15rem),1fr))]"
      role="status"
      aria-label="Loading diagrams"
      aria-hidden
    >
      {Array.from({ length: 8 }, (_, index) => (
        <CardShell key={index}>
          <Skeleton className="aspect-[4/3] w-full" />
          <div className="flex flex-col gap-1.5 border-t border-border p-3">
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-3 w-1/3" />
          </div>
        </CardShell>
      ))}
    </div>
  );
}
