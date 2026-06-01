"use client";
import type { CommitSummaryView } from "@/lib/grove/types";
import { RelativeTime } from "@/components/shared/relative-time";
import { ScrollArea } from "@/components/ui/scroll-area";

interface Props {
  commits: CommitSummaryView[] | undefined;
  isLoading?: boolean;
}

/**
 * Comprehensive commit list. shadcn ScrollArea provides the scrollbar
 * (cross-browser-consistent, theme-aware). Each row is a left-rule
 * marker — VS Code source-control / git-graph styling — sha + relative
 * time on top, full subject below.
 */
export function CommitList({ commits, isLoading }: Props) {
  if (isLoading && !commits) {
    return <p className="text-sm text-muted-foreground">Loading commits…</p>;
  }
  if (!commits || commits.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No commits in this workspace yet.
      </p>
    );
  }
  // `flex h-full min-h-0` so the ScrollArea below can flex-1 inside a
  // viewport-fill parent (the Summary card on the detail page). `min-h-0`
  // is the standard escape hatch for `overflow:auto` children inside a
  // flex column — without it the child can't shrink below its content.
  return (
    <div className="flex h-full min-h-0 flex-col gap-2" data-testid="commit-list">
      <p className="text-xs text-muted-foreground">
        <span className="font-bold tabular-nums text-foreground">{commits.length}</span>{" "}
        {commits.length === 1 ? "commit" : "commits"} since fork
      </p>
      <ScrollArea className="min-h-[16rem] flex-1 pr-3">
        <ul className="space-y-3">
          {commits.map((c) => (
            <li
              key={c.sha}
              className="border-l-2 border-border pl-3 transition-colors hover:border-[var(--ref-branch)]"
            >
              <div className="flex items-baseline justify-between gap-2 text-xs text-muted-foreground">
                <span
                  title={c.sha}
                  className="font-mono tabular-nums text-[var(--ref-branch)]"
                >
                  {c.sha.slice(0, 7)}
                </span>
                <RelativeTime iso={c.committed_at} />
              </div>
              <p className="mt-0.5 break-words text-sm leading-snug text-foreground/95">
                {c.subject}
              </p>
            </li>
          ))}
        </ul>
      </ScrollArea>
    </div>
  );
}
