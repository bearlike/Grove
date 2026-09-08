"use client";

import { create } from "zustand";

/**
 * One request to annotate one staged image.
 *
 * `onSave` is how the host hands the result back WITHOUT knowing who asked:
 * the workspace composer stages files in assistant-ui's runtime and the
 * landing composer in plain React state, and the two have no common mutation
 * verb. The caller closes over whatever identity it holds (a runtime scope, an
 * index) — the same rule `AttachmentFile.onRemove` already states.
 */
export interface AnnotationRequest {
  readonly file: File;
  /** Receives the overwritten file — same bytes' origin, new name, PNG. */
  readonly onSave: (annotated: File) => void;
}

interface AnnotationUi {
  readonly request: AnnotationRequest | null;
  readonly maximized: boolean;
  open(request: AnnotationRequest, options?: { maximized?: boolean }): void;
  close(): void;
  setMaximized(maximized: boolean): void;
}

/**
 * Whether an annotation panel is open, and for which file.
 *
 * A store rather than props for `create-store.ts`'s reason: the panel is
 * mounted ONCE, in the shell, because the shell is the one surface present on
 * every route and the only ancestor that can put a second pane BESIDE the page
 * without remounting it. Its openers — a file card on the landing composer, a
 * file card on the workspace composer — sit in trees with no common parent
 * below that shell.
 */
export const useAnnotationUi = create<AnnotationUi>((set) => ({
  request: null,
  maximized: false,
  open: (request, options) => set({ request, maximized: options?.maximized ?? false }),
  close: () => set({ request: null, maximized: false }),
  setMaximized: (maximized) => set({ maximized }),
}));
