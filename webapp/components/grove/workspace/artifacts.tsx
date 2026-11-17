"use client";

import { useState } from "react";
import { ExternalLinkIcon, LinkIcon, PaperclipIcon, PlusIcon, Trash2Icon } from "lucide-react";

import { CardScroll, SectionCard } from "@/components/grove/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { artifactDetail, artifactFaviconUrl, artifactName } from "@/lib/grove/adapters";
import type { ArtifactRef, WorkspaceStateView } from "@/lib/grove/api";
import { useAttachArtifact, useDetachArtifact } from "@/lib/grove/hooks";
import { cn } from "@/lib/utils";
import { ArtifactViewer } from "./artifact-viewer";
import { FileTypeIcon } from "./file-type-icon";

/** The record fields an artifact surface reads: the list, and what makes a path relative. */
type ArtifactOwner = Pick<WorkspaceStateView, "id" | "artifact_refs" | "worktree_path" | "repo_root">;

const ROW = "flex w-full min-w-0 items-center gap-2 px-3 py-2 text-start";
// Same inset, opaque focus mark a ticket row carries; see `TicketRow` for why.
// The Controls tab's own focus contract (`tests/unit/controls-focus.test.ts`).
const CONTROL_FOCUS = "min-h-[24px] border focus-visible:border-ring focus-visible:ring-ring/50";
const ROW_ACTION =
  "transition-colors hover:bg-muted/50 focus-visible:inset-ring-2 focus-visible:inset-ring-ring focus-visible:outline-none";

/**
 * The files and links this workspace handed back: a plan, a report, a diagram,
 * an external document. Sits directly under Tickets, because a ticket says what
 * the work is about and an artifact is what the work produced.
 *
 * Mounted only when something is attached. A READ, like the rest of the Info tab. Opening one is the only action here.
 * Attach and detach live on Controls (`ArtifactControlsCard`).
 *
 * Owner-only: a file artifact is a host path, and the public share payload
 * carries no artifacts at all.
 */
export function ArtifactsCard({ state }: { state: ArtifactOwner }) {
  const refs = state.artifact_refs ?? [];
  const roots = [state.worktree_path, state.repo_root];
  const [open, setOpen] = useState<ArtifactRef | null>(null);

  // Nothing attached draws nothing: Info is the densest surface in the app,
  // and the Controls card is where the feature is found and used.
  if (refs.length === 0) return null;

  return (
    <SectionCard
      icon={<PaperclipIcon />}
      title="Artifacts"
      action={
        <span className="text-xs tabular-nums text-content-tertiary" data-testid="artifact-count">
          {refs.length} attached
        </span>
      }
      flush
      className="@xl:col-span-2"
      data-testid="artifacts-card"
    >
      <CardScroll className="max-h-80">
        <ul className="divide-y divide-border" data-testid="artifact-refs">
          {refs.map((ref) => (
            <li key={ref.id}>
              {ref.kind === "link" ? (
                <a
                  href={ref.target}
                  target="_blank"
                  rel="noopener noreferrer"
                  title={ref.target}
                  className={cn(ROW, ROW_ACTION)}
                  data-testid="artifact-row"
                >
                  <ArtifactLabel artifact={ref} roots={roots} />
                  <ExternalLinkIcon aria-hidden className="size-3.5 shrink-0 text-content-tertiary" />
                </a>
              ) : (
                <button
                  type="button"
                  onClick={() => setOpen(ref)}
                  title={artifactDetail(ref, roots)}
                  className={cn(ROW, ROW_ACTION)}
                  data-testid="artifact-row"
                >
                  <ArtifactLabel artifact={ref} roots={roots} />
                </button>
              )}
            </li>
          ))}
        </ul>
      </CardScroll>
      <ArtifactViewer
        workspaceId={state.id}
        artifact={open}
        detail={open ? artifactDetail(open, roots) : ""}
        onClose={() => setOpen(null)}
      />
    </SectionCard>
  );
}

/**
 * Attach and detach, on Controls because Info holds no verbs.
 *
 * One field takes either kind: the daemon decides whether the text is a link
 * or a path and refuses what it will not serve, so its message is shown as is.
 */
export function ArtifactControlsCard({ state }: { state: ArtifactOwner }) {
  const refs = state.artifact_refs ?? [];
  const roots = [state.worktree_path, state.repo_root];
  const attach = useAttachArtifact(state.id);
  const detach = useDetachArtifact(state.id);
  const [target, setTarget] = useState("");
  const [title, setTitle] = useState("");
  const error = attach.error ?? detach.error;

  return (
    <SectionCard
      icon={<PaperclipIcon />}
      title="Artifacts"
      description="Attach a file path or a link. It opens from the Info tab."
      className="@xl:col-span-2"
      data-testid="artifact-controls"
    >
      <form
        className="flex min-w-0 flex-wrap items-center gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          const trimmed = target.trim();
          if (trimmed === "") return;
          attach.mutate(
            { target: trimmed, ...(title.trim() ? { title: title.trim() } : {}) },
            {
              onSuccess: () => {
                setTarget("");
                setTitle("");
              },
            },
          );
        }}
      >
        <Input
          value={target}
          onChange={(event) => setTarget(event.target.value)}
          placeholder="docs/plan.md or https://…"
          aria-label="File path or link"
          className="min-w-40 flex-2 font-mono text-xs"
          data-testid="artifact-target"
        />
        <Input
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          placeholder="Title (optional)"
          aria-label="Title"
          className="min-w-32 flex-1 text-xs"
        />
        <Button
          type="submit"
          size="sm"
          variant="outline"
          disabled={attach.isPending || target.trim() === ""}
          className={CONTROL_FOCUS}
          data-testid="artifact-attach"
        >
          <PlusIcon aria-hidden />
          Attach
        </Button>
      </form>
      {error && (
        <p role="status" className="text-xs text-destructive" data-testid="artifact-error">
          {error.message}
        </p>
      )}
      {refs.length > 0 && (
        <ul className="divide-y divide-border">
          {refs.map((ref) => (
            <li key={ref.id} className="flex min-w-0 items-center gap-2 py-1.5">
              <ArtifactLabel artifact={ref} roots={roots} />
              <Button
                size="sm"
                variant="outline"
                disabled={detach.isPending}
                onClick={() => detach.mutate(ref.id)}
                className={CONTROL_FOCUS}
                data-testid="artifact-detach"
              >
                <Trash2Icon aria-hidden />
                Detach
              </Button>
            </li>
          ))}
        </ul>
      )}
    </SectionCard>
  );
}

/** One artifact's mark, name and location: the same three things on every surface. */
function ArtifactLabel({ artifact, roots }: { artifact: ArtifactRef; roots: readonly string[] }) {
  return (
    <>
      <ArtifactMark artifact={artifact} />
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="truncate text-sm text-content-primary">{artifactName(artifact)}</span>
        <span className="truncate font-mono text-xs text-content-tertiary">
          {artifactDetail(artifact, roots)}
        </span>
      </span>
    </>
  );
}

/**
 * A file's type icon, or a link's own favicon.
 *
 * The favicon is a plain image from the linked site, and it can be missing, so
 * a failed load swaps in the generic link mark at the same footprint. The
 * failure is remembered per URL: a re-render must not retry a dead icon.
 */
function ArtifactMark({ artifact }: { artifact: ArtifactRef }) {
  const favicon = artifactFaviconUrl(artifact);
  const [failed, setFailed] = useState<string | null>(null);
  if (artifact.kind === "file") return <FileTypeIcon path={artifact.target} />;
  if (favicon === null || failed === favicon) {
    return <LinkIcon aria-hidden className="size-4 shrink-0 text-content-tertiary" data-testid="artifact-link-icon" />;
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element -- a 16px third-party favicon; the image optimizer would need every linked host allow-listed
    <img
      src={favicon}
      alt=""
      aria-hidden
      loading="lazy"
      referrerPolicy="no-referrer"
      onError={() => setFailed(favicon)}
      className="size-4 shrink-0"
      data-testid="artifact-favicon"
    />
  );
}
