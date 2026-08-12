"use client";

import { lazy, Suspense, useMemo, useState, type ReactNode } from "react";
import { FileDiffIcon, ScissorsIcon } from "lucide-react";

import { Badge } from "@/components/assistant-ui/badge";
import { FileRowSummary } from "./file-row";
import { EmptyState, EmptyStateGreeting } from "@/components/elements/empty-state";
import { CardDisclosure } from "@/components/grove/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useWorkspaceDiff } from "@/lib/grove/hooks";
import {
  filesSource,
  totalsOf,
  type DiffUnavailableReason,
  type FileDiffEntry,
  type FilesSource,
} from "./files-source";
import { CardGrid, SectionCard } from "@/components/grove/card";

/**
 * `DiffViewer` only ever renders inside `FileDiffRow`'s `CardDisclosure`,
 * which starts (and on a large diff, almost always stays) closed — same
 * shape as `code-block.tsx`'s highlighter, so the same fix applies:
 * `React.lazy`, not `next/dynamic`'s `loading` slot, because the fallback
 * below needs THIS row's own `patch` text, which that slot has no access
 * to. Smaller win than the highlighter — `diff` + `parse-diff` are tens of
 * KB, not 785 — but it is dead weight on the same critical path for the
 * same reason: most rows in a large diff are never opened.
 */
const LazyDiffViewer = lazy(() =>
  import("@/components/assistant-ui/diff-viewer").then((m) => ({ default: m.DiffViewer })),
);

/**
 * The workspace's uncommitted changes, straight from `git diff`.
 *
 * Scope is working tree vs HEAD including untracked files — the same set
 * `peek`'s `dirty_files` counts, and a different axis from the Changes tab,
 * which compares the committed branch against its base. The two answer "what
 * have I not committed" and "what has this branch done"; neither summarises the
 * other.
 */
export function FilesTab({ workspaceId }: { workspaceId: string }) {
  const { data, isLoading } = useWorkspaceDiff(workspaceId);
  const source = useMemo(() => filesSource(data), [data]);

  if (isLoading && !data) return <FilesSkeleton />;
  return <FilesView source={source} />;
}

/**
 * The card that is about to be here, at the size it will be.
 *
 * A single grey block used to stand in for a headed card of file rows, so the
 * whole tab re-laid out the moment `git diff` answered. The shape below is the
 * real anatomy — the header band's title and totals, then one bar per row — and
 * the row height matches `FileRowSummary`'s, so the only thing that changes when
 * the data lands is what the bars say.
 *
 * This is the first fetch only: `useWorkspaceDiff` polls, and a refetch keeps
 * the previous patch on screen rather than flashing back to this.
 *
 * Exported for the same reason `FilesView` is: every state this tab can be in
 * should be reachable without a daemon, and the loading one was the only
 * exception.
 */
export function FilesSkeleton() {
  return (
    <CardGrid data-testid="files-tab" data-source="loading">
      <SectionCard
        icon={<FileDiffIcon />}
        title={<Skeleton className="h-3.5 w-32" />}
        description={<Skeleton className="h-3 w-64" />}
        action={<Skeleton className="h-3.5 w-16" />}
        flush
      >
        <div className="divide-y divide-border" aria-hidden>
          {Array.from({ length: 5 }, (_, index) => (
            <div key={index} className="flex items-center gap-2 px-3 py-2">
              <Skeleton className="size-3.5 shrink-0" />
              <Skeleton className="h-3.5 min-w-0 flex-1" />
              <Skeleton className="h-3 w-12 shrink-0" />
            </div>
          ))}
        </div>
      </SectionCard>
    </CardGrid>
  );
}

/**
 * The list itself, taking its source as a prop so every state is reachable
 * without a daemon.
 *
 * EVERY file is listed and every one starts collapsed. This tab used to render
 * up to twenty complete unified diffs on mount — 25k line rows and 12 MB of
 * markup for a large session — and capped the list at twenty to survive that,
 * silently hiding the rest. Rendering a diff only when its row is opened
 * removes both problems: the cap is gone because there is nothing left to cap.
 */
export function FilesView({ source }: { source: FilesSource }) {
  if (source.kind === "unavailable") return <NoDiff reason={source.reason} />;
  if (source.kind === "clean") return <CleanTree />;

  const totals = totalsOf(source.entries);
  const count = source.entries.length;

  return (
    <CardGrid data-testid="files-tab" data-source="diff">
      <SectionCard
        icon={<FileDiffIcon />}
        title={`${count} changed file${count === 1 ? "" : "s"}`}
        description="Uncommitted changes in the working tree, as git reports them."
        action={
          <span className="font-mono text-xs tabular-nums" data-testid="file-edit-totals">
            <span className="text-success">+{totals.additions}</span>{" "}
            <span className="text-destructive">−{totals.deletions}</span>
          </span>
        }
        flush
      >
        {/* A cut patch must say so. The daemon bounds `git diff` at 1 MB on a
            whole-file boundary, so the files below are complete but the LIST is
            not — silence here would read as "this is everything". */}
        {source.truncated && (
          <p
            className="flex items-center gap-2 border-b border-border px-3 py-2 text-xs text-muted-foreground"
            data-testid="diff-truncated"
          >
            <ScissorsIcon className="size-3.5 shrink-0" aria-hidden />
            The diff was too large to send whole. These files are complete; later ones are missing.
          </p>
        )}

        {/* No inner scroll: this card IS the tab, so bounding it would strand
            dead space under a clipped row and put a second scrollbar inside the
            panel's own. `CardGrid` already scrolls. */}
        <div className="divide-y divide-border">
          {source.entries.map((entry) => (
            <FileDiffRow key={entry.path} entry={entry} />
          ))}
        </div>
      </SectionCard>
    </CardGrid>
  );
}

/** A clean tree — git answered, and the answer is "nothing". */
function CleanTree() {
  return (
    <CardGrid
      className="place-content-center justify-items-center"
      data-testid="files-tab"
      data-source="clean"
    >
      <EmptyState className="gap-2">
        <EmptyStateGreeting className="text-xl">No uncommitted changes.</EmptyStateGreeting>
        <p className="max-w-prose text-center text-xs text-muted-foreground">
          The working tree matches the last commit. The Changes tab shows what this branch has
          committed so far.
        </p>
      </EmptyState>
    </CardGrid>
  );
}

/**
 * git could not answer, so the tab says which reason it was.
 *
 * A different statement from "no changes", and it must never share copy with
 * it: one means there is nothing to see, the other means we cannot see.
 */
function NoDiff({ reason }: { reason: DiffUnavailableReason }) {
  return (
    <CardGrid
      className="place-content-center justify-items-center"
      data-testid="files-tab"
      data-source="unavailable"
      data-reason={reason}
    >
      <EmptyState className="gap-2">
        <EmptyStateGreeting className="text-xl">No diff available here.</EmptyStateGreeting>
        <p className="max-w-prose text-center text-xs text-muted-foreground">{REASONS[reason]}</p>
        <Badge variant="muted" size="sm" className="font-mono">
          {reason}
        </Badge>
      </EmptyState>
    </CardGrid>
  );
}

/**
 * One line per reason the daemon can give, keyed by the wire's own enum so a
 * new reason fails typecheck here rather than rendering as a blank paragraph.
 */
const REASONS: Record<DiffUnavailableReason, string> = {
  worktree_missing:
    "This workspace's worktree is gone from disk, so there is nothing to diff. Respawning the workspace recreates it.",
  not_a_repo:
    "This workspace is not a git repository, so there is no diff to show. Everything else about the workspace still works.",
  git_failed: "git could not read this working tree. The daemon log has the underlying error.",
  unknown: "Grove could not reach the daemon for this workspace's diff.",
};

/**
 * One file: a path and its tallies always, the diff only while open.
 *
 * A row rather than a card of its own — these live inside one card's divided
 * list, which is the one place `CardDisclosure` is used without a `CardShell`
 * around it.
 */
function FileDiffRow({ entry }: { entry: FileDiffEntry }) {
  const [open, setOpen] = useState(false);

  return (
    <CardDisclosure
      open={open}
      onOpenChange={setOpen}
      data-testid="file-diff-row"
      contentClassName="px-3 pb-3"
      summary={
        <FileRowSummary
          path={entry.displayPath}
          additions={entry.additions}
          deletions={entry.deletions}
        />
      }
    >
      {/* `showIcon` off: the row above carries the coloured type icon, and the
          vendored header's own badge is the monochrome chip it replaces. */}
      <Suspense fallback={<RawPatch patch={entry.patch} />}>
        <LazyDiffViewer patch={entry.patch} viewMode="unified" size="sm" showIcon={false} />
      </Suspense>
    </CardDisclosure>
  );
}

/**
 * Mirrors `DiffViewer`'s own base classes as closely as `lint:styling`
 * allows — `border font-mono text-xs` for the `default`/`sm` combination
 * this row always requests — so the swap to a real diff changes colour
 * first, not geometry outright. `rounded-lg` is dropped even though the
 * vendored component sets it: `components/grove/` may not carry a radius
 * utility itself (see `webapp/CLAUDE.md`'s vendored-verbatim rule), and
 * this file has no vendored wrapper to source one from the way `CardShell`
 * does — the fallback squares off for the instant it exists. Full pixel
 * parity isn't possible anyway without duplicating `DiffViewer`'s per-line
 * layout, and that isn't worth doing for a fallback — say so rather than
 * pretend: a hunk header, per-line +/- markers and a line-number gutter all
 * appear once the real diff lands, so the block's height still moves.
 */
function RawPatch({ patch }: { patch: string }): ReactNode {
  return (
    <pre className="overflow-hidden overflow-x-auto border bg-background p-4 font-mono text-xs wrap-break-word whitespace-pre-wrap">
      {patch}
    </pre>
  );
}
