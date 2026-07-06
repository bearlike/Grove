"use client";
import type { CommitSummaryView } from "@/lib/grove/types";
import { RelativeTime } from "@/components/shared/relative-time";
import { parseCommitSubject } from "@/lib/grove/commit-format";

interface Props {
  commits: CommitSummaryView[] | undefined;
  isLoading?: boolean;
}

/**
 * Comprehensive commit list. Scrolls under the global native scrollbar rule
 * (design-direction.md §4.7, wired in app/globals.css) — no ScrollArea
 * wrapper needed. Each row is a left-rule marker — VS Code source-control /
 * git-graph styling — sha + relative time on top, full subject below.
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
  // `flex h-full min-h-0` so the scroll container below can flex-1 inside a
  // viewport-fill parent (the Summary card on the detail page). `min-h-0`
  // is the standard escape hatch for `overflow:auto` children inside a
  // flex column — without it the child can't shrink below its content.
  return (
    <div className="flex h-full min-h-0 flex-col gap-2" data-testid="commit-list">
      <p className="text-xs text-muted-foreground">
        <span className="font-bold tabular-nums text-foreground">{commits.length}</span>{" "}
        {commits.length === 1 ? "commit" : "commits"} since fork
      </p>
      <div className="min-h-[16rem] flex-1 overflow-y-auto pr-3">
        <ul className="space-y-3">
          {commits.map((c) => {
            // Strip the leading gitmoji + surface the conventional-commit type as
            // a small muted tag (issue #96): rationed color, no decorative emoji.
            const { tag, subject } = parseCommitSubject(c.subject);
            return (
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
                  {tag && (
                    <span className="mr-1.5 font-mono text-xs text-muted-foreground">{tag}</span>
                  )}
                  {subject}
                </p>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
