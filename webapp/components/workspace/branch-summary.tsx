"use client";

import { useTheme } from "next-themes";
import { ArrowDown, ArrowUp, ChevronDown, Square } from "lucide-react";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { StatTrio } from "@/components/workspace/stat-trio";
import { CommitList } from "@/components/workspace/commit-list";
import { statColor } from "@/lib/grove/status-tokens";
import { cn } from "@/lib/utils";
import type { CommitSummaryView, WorkspacePeekView } from "@/lib/grove/types";

const SECTION_LABEL =
  "text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground";

/**
 * The branch summary, collapsed into a popover so the detail page can spend its
 * full width on the agent surface (the context-efficiency goal of the revamp).
 * The trigger is a glanceable ahead/behind/dirty chip; the content reuses the
 * same `StatTrio` + diff line + `CommitList` that used to occupy a standing
 * column — no bespoke chrome, the popover is the only new container.
 *
 * Test seam: `branch-summary-trigger` opens `branch-summary`; inside live the
 * unchanged `stat-trio` / `commit-list` seams.
 */
export function BranchSummary({
  peek,
  commits,
  commitsLoading,
}: {
  peek: WorkspacePeekView;
  commits: CommitSummaryView[] | undefined;
  commitsLoading?: boolean;
}) {
  const { resolvedTheme } = useTheme();
  const dark = resolvedTheme === "dark";
  const ahead = peek.base_ahead;
  const behind = peek.base_behind;
  const dirty = peek.dirty_files;
  const changed = peek.diff_added > 0 || peek.diff_removed > 0;

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          data-testid="branch-summary-trigger"
          aria-label="Branch summary"
          className="inline-flex h-8 shrink-0 items-center gap-2.5 rounded-md border border-border bg-muted/40 px-2.5 font-mono text-xs transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
        >
          <Stat icon={<ArrowUp className="size-3" />} value={ahead} color={statColor("ahead", ahead, dark)} />
          <Stat icon={<ArrowDown className="size-3" />} value={behind} color={statColor("behind", behind, dark)} />
          <Stat
            icon={<Square className="size-2.5" fill="currentColor" />}
            value={dirty}
            color={statColor("dirty", dirty, dark)}
          />
          <ChevronDown className="size-3 text-muted-foreground" aria-hidden />
        </button>
      </PopoverTrigger>
      <PopoverContent
        data-testid="branch-summary"
        align="end"
        className="w-80 max-w-[calc(100vw-2rem)] p-0"
      >
        <div className="border-b border-border px-4 py-3">
          <span className={SECTION_LABEL}>Branch summary</span>
        </div>
        <div className="space-y-3 px-4 py-3">
          <StatTrio ahead={ahead} behind={behind} dirty={dirty} />
          <p className="text-xs text-muted-foreground">
            {changed ? (
              <>
                <span className="font-medium text-[var(--ref-add)]">+{peek.diff_added}</span>
                {" / "}
                <span className="font-medium text-[var(--ref-remove)]">−{peek.diff_removed}</span>
                {" lines changed"}
              </>
            ) : (
              "Working tree clean."
            )}
          </p>
        </div>
        <div className="space-y-2 border-t border-border px-4 py-3">
          <span className={SECTION_LABEL}>Commits since fork</span>
          <CommitList commits={commits} isLoading={commitsLoading} />
        </div>
      </PopoverContent>
    </Popover>
  );
}

function Stat({
  icon,
  value,
  color,
}: {
  icon: React.ReactNode;
  value: number;
  color: string;
}) {
  return (
    <span className={cn("inline-flex items-center gap-1 tabular-nums")} style={{ color }}>
      <span aria-hidden>{icon}</span>
      {value}
    </span>
  );
}
