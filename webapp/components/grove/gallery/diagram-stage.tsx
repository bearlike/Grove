"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { galleryPageDocument, galleryPages } from "@/lib/grove/adapters";

import { useDrawioFrame, type DrawioFrame } from "./drawio-frame";

export interface DiagramStageState {
  readonly frame: DrawioFrame;
  readonly attachFrame: (element: HTMLIFrameElement | null) => void;
  readonly pages: readonly { id: string; name: string }[];
  readonly current: { id: string; name: string } | null;
  readonly setPageId: (id: string) => void;
}

/**
 * One read-only draw.io document on screen: the frame, its page choice and the
 * fit that follows a resize.
 *
 * The gallery lightbox and the artifact viewer both show a `.drawio` this way,
 * so the three effects live here once. Read-only by construction: the frame is
 * the chromeless viewer and nothing here posts a save.
 *
 * The page tabs are Grove's, not the editor's. The chromeless embed draws none
 * and the protocol's `load` takes no page selector, so switching a page hands
 * the frame a one-page document (`galleryPageDocument`).
 *
 * @param base The draw.io deployment, or `null` when none is configured.
 * @param xml The document source, or `undefined` while it loads.
 * @param documentKey Changes when a different document is shown, which resets
 *   the page choice to the first page.
 */
export function useDiagramStage(
  base: string | null,
  xml: string | undefined,
  documentKey: string | null,
): DiagramStageState {
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
  const pages = useMemo(() => (xml ? galleryPages(xml) : []), [xml]);
  const [pageId, setPageId] = useState<string | null>(null);
  const current = pages.find((page) => page.id === pageId) ?? pages[0] ?? null;

  // A new document starts on its first page; the reader's page choice does
  // not carry from one diagram to the next.
  useEffect(() => {
    setPageId(null);
  }, [documentKey]);

  useEffect(() => {
    if (!xml || current === null) return;
    const page = pages.length > 1 ? galleryPageDocument(xml, current.id) : xml;
    if (page !== null) void frame.load(page).then(() => frame.fit());
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the DOCUMENT and the PAGE are the triggers; the frame object is stable per mount
  }, [xml, current?.id]);

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

  return { frame, attachFrame, pages, current, setPageId };
}

/** The page tabs, shown only for a document with more than one page. */
export function DiagramPageTabs({
  stage,
  testId,
}: {
  stage: DiagramStageState;
  testId: string;
}): React.ReactNode {
  if (stage.pages.length <= 1 || !stage.current) return null;
  return (
    <Tabs value={stage.current.id} onValueChange={stage.setPageId} className="shrink-0">
      <TabsList variant="line" className="h-8 max-w-full overflow-x-auto" aria-label="Pages">
        {stage.pages.map((page) => (
          <TabsTrigger key={page.id} value={page.id} className="flex-none text-xs" data-testid={testId}>
            {page.name}
          </TabsTrigger>
        ))}
      </TabsList>
    </Tabs>
  );
}

/** The frame itself, under a skeleton until the editor reports the document loaded. */
export function DiagramFrame({
  stage,
  frameKey,
  title,
  testId,
}: {
  stage: DiagramStageState;
  /** Remounts the frame for a different document. */
  frameKey: string;
  title: string;
  testId: string;
}): React.ReactNode {
  return (
    <>
      {!stage.frame.ready ? <Skeleton className="absolute inset-0" aria-hidden /> : null}
      <iframe
        key={frameKey}
        ref={stage.attachFrame}
        src={stage.frame.src}
        title={title}
        className="h-full w-full border-0"
        data-testid={testId}
      />
    </>
  );
}
