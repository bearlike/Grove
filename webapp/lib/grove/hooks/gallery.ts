"use client";

import { useMutation, useQuery, useQueryClient, type UseQueryResult } from "@tanstack/react-query";

import type {
  GalleryDocumentView,
  GalleryItemView,
  GalleryPreviewView,
} from "@/lib/grove/api";
import { groveClient, POLL_MS } from "./client";
import { groveKeys } from "./keys";
import { backstopInterval, useActivityStream } from "./stream";

/**
 * Every `.drawio` in the owned catalog.
 *
 * Catalog source watching publishes `catalog_changed` for every relevant file
 * mutation, so the listing refreshes from that edge and polls only while the
 * main stream is unavailable.
 */
export function useGallery(): UseQueryResult<GalleryItemView[]> {
  const { connected } = useActivityStream();
  return useQuery({
    queryKey: groveKeys.gallery(),
    queryFn: () => groveClient.getGallery(),
    refetchInterval: backstopInterval(connected, POLL_MS.catalog),
  });
}

/** One diagram's XML, fetched on demand — for the viewer, the download and the hidden renderer. */
export function useGalleryDocument(id: string | null): UseQueryResult<GalleryDocumentView> {
  return useQuery({
    queryKey: groveKeys.galleryDocument(id ?? ""),
    queryFn: () => groveClient.getGalleryDocument(id ?? ""),
    enabled: id !== null,
    staleTime: 60_000,
  });
}

/**
 * The cached first-page PNG for one content digest.
 *
 * Keyed by digest, not only by id, so a file that changed on disk fetches a
 * fresh picture instead of serving the old one from the query cache. A 404 is
 * the ordinary answer for a file nobody has rendered yet, so it is `enabled`
 * only once the listing says the daemon holds one.
 */
export function useGalleryPreview(
  id: string,
  digest: string,
  ready: boolean,
): UseQueryResult<GalleryPreviewView> {
  return useQuery({
    queryKey: groveKeys.galleryPreview(id, digest),
    queryFn: () => groveClient.getGalleryPreview(id),
    enabled: ready,
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
  });
}

/** Post a browser-rendered PNG for one item and mark the listing stale so it reports `preview_ready`. */
export function useSaveGalleryPreview() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, digest, contentBase64 }: { id: string; digest: string; contentBase64: string }) =>
      groveClient.saveGalleryPreview(id, { digest, content_base64: contentBase64 }),
    onSuccess: (view, { id }) => {
      queryClient.setQueryData(groveKeys.galleryPreview(id, view.digest), view);
      void queryClient.invalidateQueries({ queryKey: groveKeys.gallery() });
    },
  });
}
