"use client";

import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { StatusBadge } from "@/components/workspace/status-badge";
import { PlacementBadge } from "@/components/workspace/placement-badge";
import { AgentStateBadge } from "@/components/dashboard/agent-state-badge";
import { AgentStateMark } from "@/components/shared/state-mark";
import { BranchSummary } from "@/components/workspace/branch-summary";
import { WorkspaceActions } from "@/components/workspace/workspace-actions";
import { cn } from "@/lib/utils";
import type { AgentLiveStatus } from "@/lib/grove/agent-activity";
import type { CommitSummaryView, WorkspacePeekView } from "@/lib/grove/types";

/**
 * The workspace context header (Tier 2 of the shell, under the app header):
 * one compact band that identifies the session — title, branch ▸ base, agent —
 * carries its live state badge and "happening now" line, and folds the whole
 * branch summary into a popover. It replaces the old standing identity + summary
 * cards; collapsing them here is what frees the page's full width for the agent
 * surface.
 *
 * Layering (issue #90): this band is the HEADER STRIP of the detail panel — the
 * top of the same lifted `bg-card` well that holds the transcript — so identity
 * and transcript read as one bordered card. It carries the panel's top rounding
 * (`rounded-t-lg`), a `border-b` divider, and the subtle-well `bg-muted/40`
 * surface that sits one tier above the canvas and below the card body.
 *
 * The live state badge is the agent axis (`AgentStateBadge`) whenever a session
 * exists, falling back to the workspace `StatusBadge` only when there is none —
 * the same "agent state is the headline, lifecycle is the fallback" rule the
 * dashboard card follows.
 *
 * Test seam: `context-bar`, `context-task` (the live line); identity/summary
 * seams come from the composed badges and `branch-summary`.
 */
export function ContextBar({
  peek,
  live,
  commits,
  commitsLoading,
  onKilled,
}: {
  peek: WorkspacePeekView;
  live: AgentLiveStatus;
  commits: CommitSummaryView[] | undefined;
  commitsLoading?: boolean;
  /** After a successful kill the page navigates home (the peek would 404). */
  onKilled?: () => void;
}) {
  const s = peek.state;
  return (
    <section
      data-testid="context-bar"
      className="flex shrink-0 flex-col gap-2.5 rounded-t-lg border-b border-border bg-muted/40 px-4 py-3"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <Button asChild variant="ghost" size="icon-sm" className="shrink-0">
          <Link href="/" aria-label="Back to all workspaces">
            <ArrowLeft />
          </Link>
        </Button>
        <h1
          className="min-w-0 flex-1 truncate text-sm font-semibold tracking-tight sm:text-base"
          title={s.description ?? s.title}
        >
          {s.title}
        </h1>
        <div className="flex shrink-0 items-center gap-1.5">
          <PlacementBadge placement={s.placement} />
          {live.hasSession ? (
            <AgentStateBadge state={live.state} />
          ) : (
            <StatusBadge status={s.status} size="sm" />
          )}
          <BranchSummary peek={peek} commits={commits} commitsLoading={commitsLoading} />
        </div>
        {/* Lifecycle controls live with the identity/state cluster — gated by
            status + placement, kill behind an explicit confirm. */}
        <WorkspaceActions state={s} onKilled={onKilled} />
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 pl-0 sm:pl-9">
        <span className="flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
          <Badge
            variant="outline"
            className="max-w-[14rem] truncate border-[var(--ref-branch)]/40 font-mono text-[var(--ref-branch)]"
            title={s.branch}
          >
            {s.branch}
          </Badge>
          <span className="text-muted-foreground/60">from</span>
          <Badge variant="outline" className="font-mono">
            {s.base_branch}
          </Badge>
        </span>
        <span aria-hidden className="hidden text-muted-foreground/40 sm:inline">
          ·
        </span>
        <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <span className="text-muted-foreground/60">agent</span>
          <Badge
            variant="outline"
            className="border-[var(--ref-info)]/40 font-mono text-[var(--ref-info)]"
          >
            {s.agent_name}
          </Badge>
        </span>

        {/* Stable live region: always mounted so a screen reader announces
            content *changes* in place. Mounting it conditionally would either
            miss the announcement or re-announce on every poll that briefly
            races the task to null. `aria-atomic` reads the whole phrase. */}
        <span
          data-testid="context-task"
          aria-live="polite"
          aria-atomic="true"
          className={cn(
            "ml-auto inline-flex min-w-0 max-w-full items-center gap-2 rounded-full px-2.5 py-1 sm:max-w-[26rem]",
            // Neutral well — the state hue rides only the unified glyph, never
            // the chrome (color discipline: terracotta is the `you` label + the
            // composer CTA, nothing here).
            live.taskLine && "border border-border bg-muted/40",
          )}
        >
          {live.taskLine && (
            <>
              <AgentStateMark state={live.state} className="shrink-0 text-xs" />
              <span className="min-w-0 truncate font-mono text-[11px] text-foreground">
                {live.taskLine}
              </span>
              {live.subagents > 0 && (
                <span className="shrink-0 whitespace-nowrap text-[11px] text-muted-foreground">
                  · {live.subagents} bg
                </span>
              )}
            </>
          )}
        </span>
      </div>
    </section>
  );
}
