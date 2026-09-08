"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import {
  DownloadIcon,
  Maximize2Icon,
  RotateCcwIcon,
  SquareIcon,
  UploadIcon,
  XIcon,
} from "lucide-react";

import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";

import { ErrorState } from "@/components/elements/error-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  configureMessage,
  draftFilename,
  drawioEmbedUrl,
  drawioOrigin,
  firstPageId,
  fitMessage,
  flushMessages,
  flushToken,
  loadMessage,
  parseDrawioEvent,
  pngBase64,
  previewMessage,
  previewToken,
  savedStatusMessage,
  unsavedStatusMessage,
} from "@/lib/grove/adapters";
import type { DiagramDocumentView, DiagramSessionView } from "@/lib/grove/api";
import { useDiagramWriter, useWorkspaceDiagram } from "@/lib/grove/hooks";
import { groveClient } from "@/lib/grove/hooks/client";
import {
  diagramCollaboration,
  type DiagramSaveState,
  type DiagramSnapshot,
} from "@/lib/grove/runtime/diagram";

import { resolvedDrawioBase } from "./diagram-config";

/** How long the editor gets to answer a flush before the caller gives up on it. */
const FLUSH_TIMEOUT_MS = 4_000;
/** How long the editor gets to say `load` before the tab reports it never came up. */
const EDITOR_READY_TIMEOUT_MS = 20_000;

/**
 * A draw.io editor over one worktree file.
 *
 * An agent's edit lands LIVE via commit-then-compare, not a blind reload:
 * draw.io emits nothing while a label is being typed, but `resetEditor`
 * commits it and `export` returns the document. Equal to the capture taken
 * when the editor loaded ⇒ untouched ⇒ adopt; otherwise a conflict that keeps
 * the reader's bytes. Compare against the EDITOR's capture, never the file —
 * a round trip rewrites metadata.
 *
 * `active` is "on screen", and it gates the poll.
 */
export function DiagramTab({
  workspaceId,
  repoRoot,
  descriptor: descriptorProp,
  active,
  onExpand,
}: {
  workspaceId: string;
  /** Required on every diagram route: the daemon resolves the project before touching the file. */
  repoRoot: string;
  descriptor: DiagramSessionView;
  active: boolean;
  onExpand?: () => void;
}) {
  const base = resolvedDrawioBase();
  const writer = useDiagramWriter(workspaceId, repoRoot);
  const collaboration = diagramCollaboration(workspaceId, writer);
  const snapshot = useSyncExternalStore(
    collaboration.subscribe,
    collaboration.getSnapshot,
    collaboration.getSnapshot,
  );
  // The SNAPSHOT's descriptor, never the prop: after a stop the machine holds
  // the mode the daemon just confirmed while the workspace record is still a
  // poll behind, and reading the prop makes the header disagree with the frame.
  const descriptor = snapshot.descriptor ?? descriptorProp;
  const watch = active && descriptor.mode === "active";
  const document = useWorkspaceDiagram(workspaceId, repoRoot, watch);

  const frame = useRef<HTMLIFrameElement | null>(null);
  const flushes = useRef(new Map<string, (xml: string | null) => void>());
  /** The exact ACK identity currently awaiting PNG export, one per revision. */
  const previews = useRef(new Set<string>());
  const previewTimers = useRef(new Map<string, number>());
  const previewDeadlines = useRef(new Map<string, number>());
  /** Render/upload failures stay visible without contaminating autosave state. */
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewRetry, setPreviewRetry] = useState(0);
  /** What the editor exports for an untouched document, captured right after load. */
  const editorBaseline = useRef<string | null>(null);
  const lastEditorSave = useRef<string | null>(null);
  const probed = useRef<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [ready, setReady] = useState(false);
  const [stalled, setStalled] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [flushError, setFlushError] = useState<string | null>(null);
  const [overwrite, setOverwrite] = useState<{ document: DiagramDocumentView; draft: string } | null>(null);
  const [preparingOverwrite, setPreparingOverwrite] = useState(false);
  /** The mode the FRAME is on. Lags the daemon's by one flush, so a stop's
   * viewer remount cannot destroy a label still being typed. */
  const [mode, setMode] = useState<DiagramSessionView["mode"]>(
    descriptorProp.mode,
  );

  useEffect(() => {
    collaboration.restore(descriptorProp);
  }, [collaboration, descriptorProp]);

  useEffect(() => {
    if (document.data) collaboration.observe(document.data);
  }, [collaboration, document.data]);

  // The poll is off in read-only, so the mode edge itself has to fetch once —
  // otherwise a stopped diagram renders whatever bytes were last polled while
  // it was still editable.
  useEffect(() => {
    void document.refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the EDGE is the trigger, not the query object
  }, [descriptorProp.mode, descriptorProp.session_id]);

  const origin = base ? drawioOrigin(base) : null;

  const post = useCallback(
    (message: string) => {
      // An EXACT origin, never `"*"`: the document is the payload.
      if (origin) frame.current?.contentWindow?.postMessage(message, origin);
    },
    [origin],
  );

  // Observe the actual iframe, so split handles, sidebar transitions and window
  // resizes share one path. Wait for settling and ignore minor shifts to leave
  // the user's zoom alone while they work. Hidden frames never receive a fit.
  useEffect(() => {
    const element = frame.current;
    if (!ready || !active || !element) return;
    let fitted: { width: number; height: number } | null = null;
    let timer: number | undefined;
    const schedule = () => {
      window.clearTimeout(timer);
      const { width, height } = element.getBoundingClientRect();
      if (width <= 0 || height <= 0) {
        fitted = null;
        return;
      }
      if (fitted && Math.abs(width - fitted.width) < Math.max(48, fitted.width * 0.1) &&
          Math.abs(height - fitted.height) < Math.max(48, fitted.height * 0.1)) return;
      timer = window.setTimeout(() => {
        const size = element.getBoundingClientRect();
        if (size.width <= 0 || size.height <= 0) return;
        fitted = { width: size.width, height: size.height };
        post(fitMessage());
      }, 250);
    };
    const observer = new ResizeObserver(schedule);
    observer.observe(element);
    schedule();
    return () => {
      observer.disconnect();
      window.clearTimeout(timer);
    };
  }, [active, ready, post, descriptor.session_id, mode, snapshot.loadToken, attempt]);

  /**
   * Commit the cell mid-edit, then read the document back.
   *
   * Correlated by echoed token, because two flushes can overlap and mean
   * opposite things; bounded, because an unanswering editor must not strand
   * the verb that asked.
   */
  const requestFlush = useCallback((): Promise<string | null> => {
    if (!origin || !frame.current?.contentWindow) return Promise.resolve(null);
    const token = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    return new Promise((resolve) => {
      const settle = (xml: string | null) => {
        if (!flushes.current.delete(token)) return;
        window.clearTimeout(timer);
        resolve(xml);
      };
      const timer = window.setTimeout(() => settle(null), FLUSH_TIMEOUT_MS);
      flushes.current.set(token, settle);
      for (const message of flushMessages(token)) post(message);
    });
  }, [origin, post]);

  // Commit and capture before the viewer remount destroys the frame. Kept for
  // download, never sent: the daemon has already stopped accepting writes.
  useEffect(() => {
    if (mode === descriptor.mode) return;
    if (descriptor.mode !== "read_only" || !ready)
      return void setMode(descriptor.mode);
    let cancelled = false;
    void requestFlush().then((xml) => {
      if (cancelled) return;
      if (xml === null) {
        setFlushError(
          "Collaboration stopped, but the editor did not return its draft. Keep this tab open and retry before reloading.",
        );
        return;
      }
      if (xml !== editorBaseline.current) collaboration.queue(xml);
      collaboration.adoptExternal();
      setMode("read_only");
    });
    return () => {
      cancelled = true;
    };
  }, [collaboration, descriptor.mode, mode, ready, requestFlush]);

  useEffect(() => {
    if (!origin) return;
    const onMessage = (event: MessageEvent) => {
      // BOTH: origin proves the host, source proves it is OUR frame.
      if (event.origin !== origin) return;
      if (event.source !== frame.current?.contentWindow) return;
      const parsed = parseDrawioEvent(event.data);
      if (parsed === null) return;
      if (parsed.kind === "configure") return post(configureMessage());
      if (parsed.kind === "init") {
        const xml = collaboration.editorSource();
        if (xml !== undefined) post(loadMessage(xml, mode));
        return;
      }
      if (parsed.kind === "load") {
        setReady(true);
        setStalled(false);
        // The editor's own rendering of an untouched document.
        editorBaseline.current = null;
        void requestFlush().then((xml) => {
          editorBaseline.current = xml;
        });
        return;
      }
      if (parsed.kind === "save") {
        lastEditorSave.current = parsed.xml;
        collaboration.queue(parsed.xml);
        return;
      }
      if (parsed.kind === "export") {
        const token = flushToken(parsed.message);
        const settle = token === null ? undefined : flushes.current.get(token);
        if (settle) {
          settle(parsed.data);
          return;
        }
        const preview = previewToken(parsed.message);
        if (preview) {
          const key = `${preview.sessionId}:${preview.revision}`;
          window.clearTimeout(previewDeadlines.current.get(key));
          previewDeadlines.current.delete(key);
        }
        const latest = collaboration.getSnapshot();
        const baseline = latest.baseline;
        const current = latest.descriptor;
        // The export response is usable only for the document identity that
        // requested it. A late first-page image must never masquerade as the
        // preview of a later acknowledged revision.
        if (
          preview === null ||
          baseline === null ||
          current === null ||
          preview.sessionId !== current.session_id ||
          preview.revision !== baseline.revision
        )
          return;
        const contentBase64 = pngBase64(parsed.data);
        if (contentBase64 === null) {
          previews.current.delete(`${preview.sessionId}:${preview.revision}`);
          setPreviewError("Preview unavailable: draw.io did not return a PNG.");
          return;
        }
        void groveClient
          .saveDiagramPreview(workspaceId, {
            session_id: preview.sessionId,
            expected_revision: preview.revision,
            content_base64: contentBase64,
          })
          .then(
            () => {
              if (collaboration.getSnapshot().baseline?.revision === preview.revision) setPreviewError(null);
            },
            (error: unknown) => {
              const now = collaboration.getSnapshot();
              if (now.baseline?.revision !== preview.revision || now.descriptor?.session_id !== preview.sessionId) return;
              previews.current.delete(
                `${preview.sessionId}:${preview.revision}`,
              );
              setPreviewError(
                `Preview unavailable: ${error instanceof Error ? error.message : "upload failed"}`,
              );
            },
          );
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [
    collaboration,
    mode,
    origin,
    post,
    requestFlush,
    snapshot.baseline,
    snapshot.descriptor,
    workspaceId,
  ]);

  // Fires on the server-acknowledged baseline, never on `editorSource`: the
  // latter may be a live, unacknowledged draft. The image is deliberately page
  // zero only. draw.io's export action cannot inspect a browserless workspace.
  useEffect(() => {
    const baseline = snapshot.baseline;
    const sessionId = snapshot.descriptor?.session_id;
    if (!ready || !origin || baseline === null || sessionId === undefined)
      return;
    const pageId = firstPageId(baseline.xml);
    if (pageId === null) {
      setPreviewError(
        "Preview unavailable: the acknowledged document has no first page.",
      );
      return;
    }
    const key = `${sessionId}:${baseline.revision}`;
    if (previews.current.has(key)) return;
    setPreviewError(null);
    const timer = window.setTimeout(() => {
      previewTimers.current.delete(key);
      // Mark at dispatch rather than scheduling. React StrictMode invokes an
      // effect cleanup before re-running it, and a scheduled mark would make
      // that development-only cleanup suppress the real export.
      if (previews.current.has(key)) return;
      previews.current.add(key);
      previewDeadlines.current.set(key, window.setTimeout(() => {
        previewDeadlines.current.delete(key);
        const current = collaboration.getSnapshot();
        if (current.baseline?.revision !== baseline.revision || current.descriptor?.session_id !== sessionId) return;
        previews.current.delete(key);
        setPreviewError("Preview unavailable: the editor did not return an image. Retry preview.");
      }, EDITOR_READY_TIMEOUT_MS));
      post(previewMessage(pageId, sessionId, baseline.revision));
    }, 300);
    previewTimers.current.set(key, timer);
    return () => {
      window.clearTimeout(timer);
      previewTimers.current.delete(key);
    };
  }, [
    origin,
    post,
    previewRetry,
    ready,
    snapshot.baseline,
    snapshot.descriptor,
  ]);

  // The live path. Ending a cell edit on an external EDGE is acceptable; on a
  // timer it would interrupt the person drawing. Once per external revision.
  useEffect(() => {
    const external = snapshot.external;
    if (!external || snapshot.draft !== null || mode === "read_only") return;
    if (!ready || probed.current === external.revision) return;
    probed.current = external.revision;
    void requestFlush().then((xml) => {
      const untouched =
        xml !== null &&
        editorBaseline.current !== null &&
        xml === editorBaseline.current;
      if (untouched) collaboration.adoptExternal();
      else if (xml !== null) collaboration.queue(xml);
    });
  }, [
    collaboration,
    mode,
    ready,
    requestFlush,
    snapshot.draft,
    snapshot.external,
  ]);

  // A fresh load is a fresh baseline capture; anything outstanding is void.
  useEffect(() => {
    setReady(false);
    setStalled(false);
    editorBaseline.current = null;
    lastEditorSave.current = null;
    probed.current = null;
    const timer = window.setTimeout(
      () => setStalled(true),
      EDITOR_READY_TIMEOUT_MS,
    );
    return () => window.clearTimeout(timer);
  }, [attempt, mode, descriptor.session_id, snapshot.loadToken]);

  // The editor's own indicator, driven by the daemon's answer.
  useEffect(() => {
    if (mode === "read_only" || !ready) return;
    if (snapshot.save === "clean") {
      // Reuse the acknowledged editor event, not a new reset/export: resetting
      // after every save would interrupt a label the person just began typing.
      if (lastEditorSave.current !== null)
        editorBaseline.current = lastEditorSave.current;
      post(savedStatusMessage());
    } else if (snapshot.save === "conflict" || snapshot.save === "error") {
      post(unsavedStatusMessage(SAVE_CHROME[snapshot.save].label));
    }
  }, [mode, post, ready, requestFlush, snapshot.save, snapshot.baseline]);

  const stop = useCallback(async () => {
    if (stopping) return;
    setStopping(true);
    setFlushError(null);
    try {
      const xml = await requestFlush();
      if (xml === null) {
        setFlushError(
          "The editor did not return its latest changes. Collaboration is still active; retry Stop editing when the editor responds.",
        );
        return;
      }
      lastEditorSave.current = xml;
      collaboration.queue(xml);
      await collaboration.stopCollaboration();
    } finally {
      setStopping(false);
    }
  }, [collaboration, requestFlush, stopping]);

  const prepareOverwrite = useCallback(async () => {
    if (preparingOverwrite) return;
    setPreparingOverwrite(true);
    setFlushError(null);
    try {
      const current = collaboration.getSnapshot();
      if (current.save !== "conflict" || current.draft === null || current.descriptor?.mode !== "active") return;
      // Preserve the retained draft: after a remount the iframe may display the
      // backend baseline rather than that recovery copy.
      const latest = await groveClient.getDiagram(workspaceId, repoRoot);
      if (latest.diagram.session_id !== current.descriptor.session_id || latest.diagram.mode !== "active") {
        setFlushError("Collaboration stopped or reopened. Your draft is retained; reopen the current diagram before choosing a replacement.");
        return;
      }
      setOverwrite({ document: latest, draft: current.draft });
    } catch (error) {
      setFlushError(error instanceof Error ? error.message : "Could not read the saved diagram.");
    } finally {
      setPreparingOverwrite(false);
    }
  }, [collaboration, preparingOverwrite, repoRoot, workspaceId]);

  const confirmOverwrite = useCallback(async () => {
    if (!overwrite) return;
    const accepted = overwrite;
    setOverwrite(null);
    const saved = await collaboration.overwriteWithDraft(accepted.document, accepted.draft);
    if (!saved) {
      setFlushError("The diagram or draft changed again, or the save failed. Your draft is retained; choose Overwrite saved diagram again to review the latest version.");
      void document.refetch();
    } else {
      setFlushError(null);
    }
  }, [collaboration, document, overwrite]);

  if (base === null) {
    return (
      <TabMessage
        title="No diagram editor is configured"
        detail="NEXT_PUBLIC_GROVE_DRAWIO_URL is set to a value this app will not load: it must be an http(s) URL with no credentials, and plain http only on loopback."
      />
    );
  }
  if (document.isError && snapshot.baseline === null) {
    return (
      <TabMessage
        title="Couldn't load this diagram"
        detail={document.error.message}
        retrying={document.isFetching}
        onRetry={() => void document.refetch()}
      />
    );
  }
  if (snapshot.baseline === null) {
    return (
      <div className="flex min-h-0 flex-1 flex-col gap-3 p-4" aria-hidden>
        <Skeleton className="h-4 w-48" />
        <Skeleton className="min-h-0 w-full flex-1" />
      </div>
    );
  }

  return (
    <div
      className="flex min-h-0 min-w-0 flex-1 flex-col"
      data-testid="diagram-tab"
    >
      <DiagramBar
        descriptor={descriptor}
        snapshot={stopping ? { ...snapshot, busy: true } : snapshot}
        editorHost={drawioHost(base)}
        stalled={stalled && !ready}
        onStop={() => void stop()}
        onDownload={() => downloadDraft(descriptor.path, snapshot.draft)}
        onReload={() => collaboration.discardDraftAndReload()}
        onOverwrite={() => void prepareOverwrite()}
        preparingOverwrite={preparingOverwrite}
        onDismiss={() => collaboration.dismissDraft()}
        onRetry={() => collaboration.retry()}
        onRemount={() => setAttempt((value) => value + 1)}
        previewError={previewError}
        onRetryPreview={() => {
          const current = collaboration.getSnapshot();
          if (current.baseline && current.descriptor) previews.current.delete(`${current.descriptor.session_id}:${current.baseline.revision}`);
          setPreviewRetry((value) => value + 1);
        }}
        onExpand={
          onExpand &&
          (() => {
            // Switching split/work reparents the panel. Capture the editor's
            // pending label before that remount can destroy it.
            void requestFlush().then((xml) => {
              if (xml === null) {
                setFlushError(
                  "The editor did not return its latest changes. Retry expanding when it responds.",
                );
                return;
              }
              if (mode === "active") collaboration.queue(xml);
              onExpand();
            });
          })
        }
      />
      <Dialog open={overwrite !== null} onOpenChange={(open) => { if (!open) setOverwrite(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Overwrite saved diagram?</DialogTitle>
            <DialogDescription>
              Replace the saved file with your retained browser draft. Backend and agent changes not in your draft will be lost. Download your draft first if you want a separate copy.
            </DialogDescription>
          </DialogHeader>
          <p className="truncate font-mono text-xs" title={overwrite?.document.diagram.path}>{overwrite?.document.diagram.path}</p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOverwrite(null)}>Cancel</Button>
            <Button variant="destructive" onClick={() => void confirmOverwrite()} data-testid="diagram-overwrite-confirm">Overwrite saved diagram</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      {flushError && (
        <p role="alert" className="px-3 text-sm">
          {flushError}
        </p>
      )}
      {/* Keyed so an adoption or a stop is a remount, not a hopeful message. */}
      <iframe
        key={`${descriptor.session_id}:${mode}:${snapshot.loadToken}:${attempt}`}
        ref={frame}
        src={drawioEmbedUrl(base, mode)}
        title={`Diagram ${descriptor.path}`}
        className="min-h-0 w-full flex-1 border-0"
        data-testid="diagram-frame"
        data-diagram-mode={mode}
      />
    </div>
  );
}

/** Reload only ever appears beside Download: it is the one control that
 * destroys work. Stop is disabled while a write is outstanding. */
function DiagramBar({
  descriptor,
  snapshot,
  editorHost,
  stalled,
  onStop,
  onDownload,
  onReload,
  onOverwrite,
  preparingOverwrite,
  onDismiss,
  onRetry,
  onRemount,
  onExpand,
  previewError,
  onRetryPreview,
}: {
  descriptor: DiagramSessionView;
  snapshot: DiagramSnapshot;
  editorHost: string;
  stalled: boolean;
  onStop: () => void;
  onDownload: () => void;
  onReload: () => void;
  onOverwrite: () => void;
  preparingOverwrite: boolean;
  onDismiss: () => void;
  onRetry: () => void;
  onRemount: () => void;
  onExpand?: () => void;
  previewError: string | null;
  onRetryPreview: () => void;
}) {
  const chrome = SAVE_CHROME[snapshot.save];
  const recoverable = snapshot.draft !== null;
  return (
    <div
      className="flex h-[32px] min-w-0 shrink-0 items-center gap-2 border-b border-border px-2"
      data-testid="diagram-toolbar"
    >
      <span
        className="min-w-0 flex-1 truncate font-mono text-xs"
        title={descriptor.path}
      >
        {descriptor.path}
      </span>
      <Badge
        variant={chrome.variant}
        className="h-5 shrink-0 px-1.5 py-0"
        data-testid="diagram-save-state"
      >
        {chrome.label}
      </Badge>
      {/* The default host is a third party and the document is sent to it. */}
      <span
        className="hidden shrink-0 text-muted-foreground text-xs xl:inline"
        title={`Editor: ${editorHost}`}
        data-testid="diagram-editor-host"
      >
        Editor: {editorHost}
      </span>
      {descriptor.mode === "read_only" && (
        <Badge variant="outline">
          Read only
        </Badge>
      )}
      {snapshot.external !== null && (
        <Badge
          variant="outline"
          data-testid="diagram-external"
        >
          Changed on disk
        </Badge>
      )}
      {stalled && (
        <Badge
          variant="destructive"
          data-testid="diagram-stalled"
        >
          Editor didn&apos;t load
        </Badge>
      )}
      <div className="flex min-w-0 shrink-0 items-center gap-1">
        {previewError && (
          <TooltipIconButton tooltip={previewError} aria-label="Retry diagram preview" onClick={onRetryPreview} data-testid="diagram-preview-error">
            <RotateCcwIcon aria-hidden />
          </TooltipIconButton>
        )}
        {stalled && (
          <TooltipIconButton
            tooltip="Retry editor"
            onClick={onRemount}
            data-testid="diagram-remount"
          >
            <RotateCcwIcon aria-hidden />
          </TooltipIconButton>
        )}
        {snapshot.save === "error" && (
          <TooltipIconButton tooltip="Retry save" onClick={onRetry}>
            <RotateCcwIcon aria-hidden />
          </TooltipIconButton>
        )}
        {recoverable && (
          <TooltipIconButton
            tooltip="Download draft"
            onClick={onDownload}
            data-testid="diagram-download"
          >
            <DownloadIcon aria-hidden />
          </TooltipIconButton>
        )}
        {(snapshot.external !== null || snapshot.save === "conflict") && (
          <TooltipIconButton
            tooltip={recoverable ? "Discard draft and reload" : "Reload"}
            onClick={onReload}
            data-testid="diagram-reload"
          >
            <RotateCcwIcon aria-hidden />
          </TooltipIconButton>
        )}
        {recoverable && snapshot.save === "conflict" && descriptor.mode === "active" && (
          <TooltipIconButton tooltip="Overwrite saved diagram with my draft" onClick={onOverwrite} disabled={preparingOverwrite || snapshot.busy} data-testid="diagram-overwrite">
            <UploadIcon aria-hidden />
          </TooltipIconButton>
        )}
        {recoverable && descriptor.mode === "read_only" && (
          <TooltipIconButton tooltip="Dismiss draft" onClick={onDismiss}>
            <XIcon aria-hidden />
          </TooltipIconButton>
        )}
        {descriptor.mode === "active" && (
          <Button
            size="xs"
            variant="outline"
            onClick={onStop}
            disabled={snapshot.busy}
            data-testid="diagram-stop"
          >
            <SquareIcon aria-hidden />
            Stop editing
          </Button>
        )}
        {onExpand && (
          <TooltipIconButton
            tooltip="Expand diagram"
            aria-label="Expand diagram"
            onClick={onExpand}
            data-testid="diagram-expand"
          >
            <Maximize2Icon aria-hidden />
          </TooltipIconButton>
        )}
      </div>
    </div>
  );
}

/** A `Record` over the union so a seventh state cannot ship unlabelled.
 * "Saved" appears once: the one state the daemon has acknowledged. */
const SAVE_CHROME: Record<
  DiagramSaveState,
  {
    label: string;
    variant: "default" | "secondary" | "outline" | "destructive";
  }
> = {
  clean: { label: "Saved", variant: "secondary" },
  dirty: { label: "Unsaved", variant: "outline" },
  saving: { label: "Saving", variant: "outline" },
  conflict: { label: "Conflict", variant: "destructive" },
  error: { label: "Not saved", variant: "destructive" },
  stopped: { label: "Stopped", variant: "outline" },
};

function TabMessage({
  title,
  detail,
  retrying = false,
  onRetry,
}: {
  title: string;
  detail: string;
  retrying?: boolean;
  onRetry?: () => void;
}) {
  return (
    <div className="flex min-h-0 flex-1 items-center justify-center p-6">
      <ErrorState
        title={title}
        detail={detail}
        retrying={retrying}
        onRetry={onRetry ?? (() => undefined)}
      />
    </div>
  );
}

/** The editor's hostname, for the disclosure. Never the full URL: it is chrome, not a link. */
function drawioHost(base: string): string {
  try {
    return new URL(base).host;
  } catch {
    return base;
  }
}

/** The escape hatch that makes every refusal above honest. No upload: this
 * must work when the daemon is exactly what is not answering. */
function downloadDraft(path: string, draft: string | null): void {
  if (draft === null || typeof window === "undefined") return;
  const url = URL.createObjectURL(
    new Blob([draft], { type: "application/xml" }),
  );
  const link = window.document.createElement("a");
  link.href = url;
  link.download = draftFilename(path);
  link.click();
  URL.revokeObjectURL(url);
}
