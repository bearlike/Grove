"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { useTheme } from "next-themes";
import type { AnnotationState } from "@markerjs/markerjs3";
import type { AnnotationEditor } from "@markerjs/markerjs-ui";

import {
  ANNOTATION_CODEC,
  annotatedExtension,
  annotatedName,
  annotatedSize,
  dataUrlMimeType,
} from "@/lib/grove/adapters";
import { cn } from "@/lib/utils";

interface Origin {
  /** The pixels the markers are drawn over — the file as the user picked it. */
  readonly source: File;
  readonly state: AnnotationState;
}

/**
 * What a file's markers were drawn FROM, so a re-edit reopens them editable
 * over the original pixels instead of drawing on top of a flattened copy.
 *
 * Keyed by the `File` object itself: a staged file lives exactly as long as
 * the composer holds it, and a `WeakMap` lets the memory die with it. Nothing
 * here is persisted — the annotation survives a re-edit within one page visit,
 * which is the only window in which the `File` exists at all.
 *
 * It ALSO carries the in-progress draft across a remount: a maximize toggle
 * moves the editor between a panel and a dialog, and a moved custom element
 * rebuilds itself from scratch in `connectedCallback`, so the markers would
 * otherwise vanish on the toggle.
 */
const ORIGINS = new WeakMap<File, Origin>();

/**
 * The marker.js editor over one staged image, as a React boundary.
 *
 * `@markerjs/markerjs-ui` registers a custom element at MODULE EVALUATION, so
 * the import is dynamic and lives inside an effect: reaching the module during
 * a server render would touch `window` and take the whole route down. The
 * element is appended by hand rather than written as JSX, because the editor
 * takes an `HTMLImageElement` PROPERTY that its `connectedCallback` reads
 * synchronously, and a property React sets after mount is one frame too late.
 *
 * `onSave` receives the OVERWRITTEN file: lossy WebP at `annotatedSize`,
 * under `annotatedName` — see `ANNOTATION_CODEC` for why the size is bounded
 * rather than natural. `onClose` is the editor's own Close button, which
 * discards the draft.
 */
export function ImageAnnotator({
  file,
  onSave,
  onClose,
  className,
}: {
  readonly file: File;
  readonly onSave: (annotated: File) => void;
  readonly onClose: () => void;
  readonly className?: string;
}): ReactNode {
  const host = useRef<HTMLDivElement | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const { resolvedTheme } = useTheme();
  const theme: "dark" | "light" = resolvedTheme === "dark" ? "dark" : "light";

  // Read through a ref so a parent re-render never tears the editor down: a
  // remount would discard every marker drawn since the last snapshot.
  const callbacks = useRef({ onSave, onClose, theme });
  callbacks.current = { onSave, onClose, theme };

  useEffect(() => {
    const container = host.current;
    if (!container) return;
    let cancelled = false;
    let element: AnnotationEditor | null = null;
    // What to fall back to if the user closes without saving: the state the
    // file was opened with, or nothing for an image never annotated.
    const opened = ORIGINS.get(file);
    const source = opened?.source ?? file;
    const url = URL.createObjectURL(source);

    void (async () => {
      const { AnnotationEditor: Editor } = await import("@markerjs/markerjs-ui");
      if (cancelled) return;
      const image = document.createElement("img");
      image.src = url;
      try {
        await image.decode();
      } catch {
        if (!cancelled) setFailure("This image could not be decoded.");
        return;
      }
      if (cancelled) return;

      element = new Editor();
      element.targetImage = image;
      element.theme = callbacks.current.theme;
      // An explicit size, not `naturalSize` and not the panel's own: the
      // former rendered every pixel of a 12 MP photo, the latter varies with
      // how wide the pane happened to be dragged.
      const size = annotatedSize(image.naturalWidth, image.naturalHeight);
      element.settings.rendererSettings.naturalSize = false;
      element.settings.rendererSettings.width = size.width;
      element.settings.rendererSettings.height = size.height;
      element.settings.rendererSettings.imageType = ANNOTATION_CODEC.mimeType;
      element.settings.rendererSettings.imageQuality = ANNOTATION_CODEC.quality;
      element.addEventListener("editorsave", (event) => {
        const { dataUrl, state } = event.detail;
        if (!dataUrl) return;
        void (async () => {
          // The extension follows the bytes PRODUCED: a browser without a WebP
          // encoder answers `toDataURL` with PNG and says nothing.
          const mimeType = dataUrlMimeType(dataUrl);
          const blob = await (await fetch(dataUrl)).blob();
          const annotated = new File([blob], annotatedName(file.name, annotatedExtension(mimeType)), {
            type: mimeType,
          });
          ORIGINS.set(annotated, { source, state });
          callbacks.current.onSave(annotated);
        })();
      });
      element.addEventListener("editorclose", () => {
        // Close discards: put back what the file was opened with, so the
        // unmount below does not snapshot the abandoned draft.
        if (opened) ORIGINS.set(file, opened);
        else ORIGINS.delete(file);
        element = null;
        callbacks.current.onClose();
      });
      element.className = "block h-full w-full";
      container.replaceChildren(element);
      // `restoreState` needs the marker area on the page — `connectedCallback`
      // builds it — so the restore follows the append.
      if (opened) element.restoreState(opened.state);
    })();

    return () => {
      cancelled = true;
      // A remount that is not a close (the maximize toggle) keeps the draft.
      if (element?.markerArea) ORIGINS.set(file, { source, state: element.markerArea.getState() });
      container.replaceChildren();
      URL.revokeObjectURL(url);
    };
  }, [file]);

  // The editor is a shadow-DOM island: the app's theme class never reaches
  // it, so the resolved theme is mirrored onto the element's own switch — at
  // creation above, and here when the theme changes under a mounted editor.
  useEffect(() => {
    const element = host.current?.firstElementChild as AnnotationEditor | null;
    if (element) element.theme = theme;
  }, [theme]);

  return (
    <div
      ref={host}
      data-testid="image-annotator"
      data-file={file.name}
      data-theme={theme}
      className={cn("relative min-h-0 min-w-0 flex-1", className)}
    >
      {failure ? (
        <p role="alert" className="text-destructive p-4 text-sm">
          {failure}
        </p>
      ) : null}
    </div>
  );
}
