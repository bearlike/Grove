"use client";

import { Maximize2Icon, Minimize2Icon, XIcon } from "lucide-react";
import { useEffect, type ReactNode } from "react";

import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { SplitHandle } from "@/components/grove/split-handle";
import { useMinWidth } from "@/components/grove/workspace/use-min-width";
import { ImageAnnotator } from "./image-annotator";
import { useAnnotationUi, type AnnotationRequest } from "./annotation-store";

/** Below this the page has no room to give up; the editor opens maximized. */
const SPLIT_MIN_WIDTH = 1024;

const PANELS = ["page", "annotation"] as const;

/**
 * The page slot, with an annotation pane BESIDE it while a request is open.
 *
 * WHY THE SHELL AND NOT THE PAGE. Both composers stage images, and the split
 * must not remount the page that holds them: the workspace transcript is tens
 * of thousands of nodes and its composer draft, staged files and runtime all
 * live inside it. A panel group whose FIRST panel is the page and whose second
 * exists only while a request is open keeps `children` at the same position
 * in the tree whether the pane is there or not — React reconciles the page in
 * place, and the landing surface and the workspace surface get the split for
 * free because the shell is the one surface present on every route.
 *
 * The maximize toggle MOVES the one editor into a dialog rather than cloning
 * it (the `ExpandedComposer` rule): a moved custom element rebuilds itself,
 * so the editor carries its own draft across the move (see `ImageAnnotator`).
 * Below `SPLIT_MIN_WIDTH` there is no pane to offer and the dialog is the only
 * host — the same breakpoint the workspace uses to withhold its own split.
 */
export function AnnotationHost({ children }: { children: ReactNode }): ReactNode {
  const request = useAnnotationUi((state) => state.request);
  const maximized = useAnnotationUi((state) => state.maximized);
  const wideEnough = useMinWidth(SPLIT_MIN_WIDTH);
  const inDialog = request !== null && (maximized || !wideEnough);
  const inPane = request !== null && !inDialog;

  return (
    <>
      {/* The library writes `data-testid` FROM `id`, after the rest props, so a
          `data-testid` prop here is silently replaced: the id is the testid
          the tour's observables and the screenshot driver locate. */}
      <ResizablePanelGroup
        id="annotation-split"
        orientation="horizontal"
        className="min-h-0 flex-1"
        data-annotating={inPane || undefined}
      >
        {/* `h-full` is load-bearing (see the workspace split): the panel wraps
            its child in its own `overflow:auto` box, and a height-less child
            grows to its full content and hands that box the scroll. */}
        <ResizablePanel id={PANELS[0]} defaultSize="55" minSize="30">
          <div className="flex h-full min-h-0 min-w-0 flex-col">{children}</div>
        </ResizablePanel>
        {inPane ? (
          <>
            <SplitHandle />
            <ResizablePanel id={PANELS[1]} defaultSize="45" minSize="25">
              <div className="flex h-full min-h-0 min-w-0 flex-col" data-testid="annotation-pane">
                <AnnotationChrome request={request} />
              </div>
            </ResizablePanel>
          </>
        ) : null}
      </ResizablePanelGroup>
      <Dialog
        open={inDialog}
        onOpenChange={(open) => {
          if (open) return;
          // Escape / the dialog's own close: on a wide window that is
          // "un-maximize", on a narrow one there is no pane to go back to.
          if (wideEnough) useAnnotationUi.getState().setMaximized(false);
          else useAnnotationUi.getState().close();
        }}
      >
        <DialogContent
          // Only layout is overridden; the vendored dialog keeps its surface.
          // Its own corner close is dropped because the chrome row below
          // already carries Close, and two identical controls a few pixels
          // apart read as a mistake.
          className="flex h-[92dvh] max-h-[92dvh] w-[calc(100vw-2rem)] flex-col gap-2 sm:max-w-[calc(100vw-2rem)]"
          showCloseButton={false}
          data-testid="annotation-expanded"
        >
          <DialogHeader>
            <DialogTitle>Annotate image</DialogTitle>
            <DialogDescription>
              Save overwrites the attached file under an annotated name.
            </DialogDescription>
          </DialogHeader>
          {inDialog ? <AnnotationChrome request={request} maximizeControl={wideEnough} /> : null}
        </DialogContent>
      </Dialog>
    </>
  );
}

/**
 * The editor plus the ONE row of chrome Grove adds above it: the file's name,
 * maximize/restore, and close. Save and the editor's own close live on the
 * vendored toolbar inside the element.
 */
function AnnotationChrome({
  request,
  maximizeControl = true,
}: {
  readonly request: AnnotationRequest;
  /** False where a restore has nowhere to go (a narrow window's dialog). */
  readonly maximizeControl?: boolean;
}): ReactNode {
  const maximized = useAnnotationUi((state) => state.maximized);
  const setMaximized = useAnnotationUi((state) => state.setMaximized);
  const close = useAnnotationUi((state) => state.close);

  return (
    <>
      <div className="flex shrink-0 items-center gap-1 px-2 py-1">
        <span className="text-content-secondary min-w-0 flex-1 truncate text-sm" title={request.file.name}>
          {request.file.name}
        </span>
        {maximizeControl ? (
          <TooltipIconButton
            tooltip={maximized ? "Restore" : "Maximize"}
            side="bottom"
            aria-label={maximized ? "Restore annotation pane" : "Maximize annotation pane"}
            className="min-h-[24px] min-w-[24px]"
            onClick={() => setMaximized(!maximized)}
            data-testid="annotation-maximize"
          >
            {maximized ? <Minimize2Icon /> : <Maximize2Icon />}
          </TooltipIconButton>
        ) : null}
        <TooltipIconButton
          tooltip="Close"
          side="bottom"
          aria-label="Close annotation pane"
          className="min-h-[24px] min-w-[24px]"
          onClick={close}
          data-testid="annotation-close"
        >
          <XIcon />
        </TooltipIconButton>
      </div>
      <ImageAnnotator
        file={request.file}
        onSave={(annotated) => {
          request.onSave(annotated);
          close();
        }}
        onClose={close}
      />
    </>
  );
}

/**
 * Close the pane when the surface that owns the staged file goes away, so a
 * navigation never leaves an editor open over a page that cannot receive its
 * save. Called ONCE per route surface, above anything a dialog moves —
 * `ExpandedComposer` unmounts the composer it expands, and a hook inside it
 * would close the pane on every expand. `key` is for a route that keeps its
 * component instance across ids (the workspace page): the files belong to
 * the id, so a change of id is a change of owner.
 */
export function useCloseAnnotationOnUnmount(key?: string): void {
  useEffect(() => () => useAnnotationUi.getState().close(), [key]);
}
