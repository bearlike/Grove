"use client";

import { useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { useTheme } from "next-themes";
import { RefreshCwIcon } from "lucide-react";

import { ErrorState } from "@/components/elements/error-state";
import { DiagramFrame, DiagramPageTabs, useDiagramStage } from "@/components/grove/gallery/diagram-stage";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { artifactIsDiagram, artifactName, artifactViewerType } from "@/lib/grove/adapters";
import type { ArtifactRef } from "@/lib/grove/api";
import { useArtifactContent } from "@/lib/grove/hooks";
import { resolvedDrawioBase } from "./diagram-config";

// Client-only and lazy: the viewer builds a shadow root and loads each format's
// renderer on demand, so neither the server render nor a page that never opens
// an artifact pays for it. The FULL package: every renderer is registered, and
// their Worker/WASM/font assets are served from `public/file-viewer/` (copied
// by `npm run assets:viewer`, see webapp/CLAUDE.md for what is left out).
const FileViewer = dynamic(() => import("@file-viewer/react-full").then((m) => m.FileViewer), {
  ssr: false,
  loading: () => <Skeleton className="absolute inset-0" aria-hidden />,
});

/**
 * One file artifact, read-only, in a lightbox.
 *
 * A `.drawio` goes to the same chromeless draw.io frame the gallery uses.
 * Everything else goes to `@file-viewer/react-full`: Office, PDF, spreadsheets
 * and CSV, archives, email, ebooks, Markdown, code, images and media, all
 * rendered in the browser with HTML sanitized.
 *
 * The bytes are read each time the dialog opens, and Refresh reads them again:
 * the artifact is a pointer to a file the agent may still be writing. There is
 * no Download here: the file viewer's own toolbar has one, and a diagram
 * downloads from the Gallery.
 */
export function ArtifactViewer({
  workspaceId,
  artifact,
  detail,
  onClose,
}: {
  workspaceId: string;
  artifact: ArtifactRef | null;
  /** The location line under the title, already made relative by the caller. */
  detail: string;
  onClose: () => void;
}): React.ReactNode {
  const content = useArtifactContent(workspaceId, artifact?.id ?? null);
  const name = artifact ? fileName(artifact.target) : "";
  const diagram = artifact !== null && artifactIsDiagram(artifact);

  return (
    <Dialog open={artifact !== null} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent
        className="flex h-[92dvh] w-[96vw] max-w-[96vw] flex-col gap-3 p-4 sm:max-w-[96vw]"
        data-testid="artifact-viewer"
      >
        <DialogHeader className="flex-row items-start gap-3 pr-8 text-start">
          <div className="min-w-0 flex-1">
            <DialogTitle className="truncate text-base">
              {artifact ? artifactName(artifact) : ""}
            </DialogTitle>
            <DialogDescription className="truncate font-mono text-xs">{detail}</DialogDescription>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            <Button
              variant="outline"
              size="sm"
              disabled={content.isFetching}
              onClick={() => void content.refetch()}
              data-testid="artifact-viewer-refresh"
            >
              <RefreshCwIcon aria-hidden />
              Refresh
            </Button>
          </div>
        </DialogHeader>
        {content.isError ? (
          <div className="relative min-h-0 flex-1 overflow-hidden border border-border">
            <ErrorState
              title="Couldn't open this artifact"
              detail={content.error.message}
              retrying={content.isFetching}
              onRetry={() => void content.refetch()}
            />
          </div>
        ) : !content.data ? (
          <div className="relative min-h-0 flex-1 overflow-hidden border border-border">
            <Skeleton className="absolute inset-0" aria-hidden />
          </div>
        ) : diagram ? (
          <DiagramBody blob={content.data} name={name} onClose={onClose} />
        ) : (
          <FileBody blob={content.data} name={name} />
        )}
      </DialogContent>
    </Dialog>
  );
}

function DiagramBody({ blob, name, onClose }: { blob: Blob; name: string; onClose: () => void }) {
  const base = resolvedDrawioBase();
  const [xml, setXml] = useState<string>();
  useEffect(() => {
    let live = true;
    void blob.text().then((text) => {
      if (live) setXml(text);
    });
    return () => {
      live = false;
    };
  }, [blob]);
  const stage = useDiagramStage(base, xml, name);

  return (
    <>
      <DiagramPageTabs stage={stage} testId="artifact-viewer-page" />
      <div className="relative min-h-0 flex-1 overflow-hidden border border-border">
        {base === null ? (
          <ErrorState
            title="No diagram viewer is configured"
            detail="NEXT_PUBLIC_GROVE_DRAWIO_URL is set to a value this app will not load."
            retrying={false}
            onRetry={onClose}
          />
        ) : (
          <DiagramFrame stage={stage} frameKey={name} title={`Diagram ${name}`} testId="artifact-viewer-frame" />
        )}
      </div>
    </>
  );
}

function FileBody({ blob, name }: { blob: Blob; name: string }) {
  const { resolvedTheme } = useTheme();
  // A stable `File` per fetch: a new object every render would reload the viewer.
  const file = useMemo(() => new File([blob], name), [blob, name]);
  // Stable too: the full component re-derives its asset options from this object.
  const options = useMemo(
    () => ({ theme: resolvedTheme === "dark" ? ("dark" as const) : ("light" as const), locale: "en" as const }),
    [resolvedTheme],
  );

  return (
    <div className="relative min-h-0 flex-1 overflow-hidden border border-border" data-testid="artifact-viewer-file">
      <FileViewer file={file} filename={name} type={artifactViewerType(name)} style={{ height: "100%" }} options={options} />
    </div>
  );
}

function fileName(target: string): string {
  return target.slice(target.lastIndexOf("/") + 1);
}
