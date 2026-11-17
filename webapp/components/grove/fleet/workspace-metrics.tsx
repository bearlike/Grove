"use client";

import { CircleIcon, ClockIcon, HourglassIcon } from "lucide-react";

import { absoluteTime, ageToken, useNow } from "@/components/grove/relative-time";
import { lastTranscriptIso } from "@/lib/grove/adapters";
import { abbreviate } from "@/components/grove/usage/format";
import { cn } from "@/lib/utils";
import type { WorkspaceActivity } from "./types";

/** One semantic treatment for a figure, never an unrelated badge per metric. */
const METRIC_TONE = {
  added: "text-success",
  removed: "text-destructive",
  pending: "text-warning",
  neutral: "text-content-secondary",
} as const;

/**
 * A count.
 *
 * `null` is UNKNOWN and renders an em dash; `0` is a real measurement and
 * renders as a zero in the quiet tier. Substituting one for the other is the
 * fabricated-zero the design system forbids — it tells a reader their branch is
 * clean when nobody looked.
 */
function Figure({
  value,
  tone = "neutral",
  prefix = "",
}: {
  value: number | null;
  tone?: keyof typeof METRIC_TONE;
  prefix?: string;
}): React.ReactNode {
  if (value === null) return <span className="text-content-tertiary">—</span>;
  return (
    <span className={cn("tabular-nums", value === 0 ? "text-content-tertiary" : METRIC_TONE[tone])}>
      {prefix}{abbreviate(value)}
    </span>
  );
}

/**
 * The rail row's second and last line: the row's claims (`context`) flowing
 * left, its age and last update flush right.
 *
 * The branch and the change ledger (dirty files, `+`/`−` deltas) used to take
 * a line each, which made every row three lines tall for figures a reader
 * scanning the rail does not act on. They remain one hover away in the row's
 * overview and on the Info and Changes tabs; the rail keeps only what ranks
 * and what asks for attention.
 */
export function SessionMetadata({
  workspace,
  context,
}: {
  workspace: WorkspaceActivity;
  context?: React.ReactNode;
}): React.ReactNode {
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1" data-testid="rail-metadata">
      <div className="min-w-0 flex-1">{context}</div>
      <SessionTimes
        createdIso={workspace.state.created_at}
        updatedIso={lastTranscriptIso(workspace)}
      />
    </div>
  );
}

/**
 * Two instants, each labelled: how OLD the workspace is, and when its
 * transcript last MOVED (#919). Replaces the single creation age, which a
 * reader took for "last active" because it sat where an activity time sits.
 *
 * The update time is the rail's sort key, so the order the rows appear in and
 * the times they print agree. The age is shown, never ranked by. A workspace
 * whose agent has not reported prints its age alone rather than a creation
 * time dressed as an update. Both are `ageToken`s (`2m`, `54d`): the glyph
 * already says "time", and "ago" on the second value widened the cell enough
 * to push the ledger's figures onto a fourth line (measured, #919). Both use
 * the shared minute clock; before mount each is a capped absolute time that
 * hydration replaces.
 */
export function SessionTimes({
  createdIso,
  updatedIso,
}: {
  createdIso: string;
  updatedIso: string | null;
}): React.ReactNode {
  const now = useNow();
  const label = updatedIso
    ? `Created ${absoluteTime(createdIso)}. Last update ${absoluteTime(updatedIso)}`
    : `Created ${absoluteTime(createdIso)}. No transcript update yet`;
  return (
    <span className="flex shrink-0 items-center gap-1 whitespace-nowrap text-content-tertiary" aria-label={label} data-testid="rail-times">
      <span className="flex items-center gap-0.5" data-testid="rail-age">
        <HourglassIcon aria-hidden className="size-3 shrink-0" />
        <span className="sr-only">Age: </span>
        <TimeValue iso={createdIso} now={now} format={ageToken} />
      </span>
      {updatedIso ? (
        <span className="flex items-center gap-0.5" data-testid="rail-updated">
          <ClockIcon aria-hidden className="size-3 shrink-0" />
          <span className="sr-only">Last update: </span>
          <TimeValue iso={updatedIso} now={now} format={ageToken} />
        </span>
      ) : null}
    </span>
  );
}

/**
 * The unread cue: new model responses since this browser last had the
 * workspace open (#919). One mark for the rail row and the fleet card, so the
 * two surfaces cannot disagree about what "new" looks like. `info` is the
 * design system's informational hue; a mark, never a rank.
 */
export function UnreadDot({ className }: { className?: string }): React.ReactNode {
  return (
    <CircleIcon
      aria-label="New responses"
      role="img"
      data-testid="unread-dot"
      className={cn("size-2.5 shrink-0 fill-info text-info", className)}
    />
  );
}

function TimeValue({
  iso,
  now,
  format,
}: {
  iso: string;
  now: number | null;
  format: (iso: string, now: number) => string;
}): React.ReactNode {
  return (
    <span className={cn("tabular-nums", now === null && "max-w-16 truncate")}>
      {now === null ? absoluteTime(iso) : format(iso, now)}
    </span>
  );
}

/** Branch deltas and worktree dirt have different scopes; their labels say so. */
export function WorkspaceMetrics({ workspace }: { workspace: WorkspaceActivity }): React.ReactNode {
  const metrics = [
    { label: "added", value: workspace.diff_added, tone: "added", prefix: "+", detail: "lines added on the branch since its diff base" },
    { label: "removed", value: workspace.diff_removed, tone: "removed", prefix: "−", detail: "lines removed on the branch since its diff base" },
    { label: "dirty files", value: workspace.dirty_files, tone: "pending", detail: "uncommitted files in the worktree" },
    { label: "ahead", value: workspace.base_ahead, tone: "added", detail: "commits ahead of the base branch (not unpushed commits)" },
    { label: "behind", value: workspace.base_behind, tone: "pending", detail: "commits behind the base branch" },
  ] as const;

  return (
    <div className="flex flex-col gap-2" data-testid="card-metrics">
      <dl className="flex flex-wrap items-center gap-x-3 gap-y-1">
        {metrics.map((metric) => (
          <div key={metric.label} className="flex min-w-0 items-baseline gap-1" title={`${metric.value} ${metric.detail}`}>
            <dt className="order-2 text-xs text-content-tertiary">{metric.label}</dt>
            <dd className="text-xs font-medium" aria-label={`${metric.value} ${metric.detail}`}>
              <Figure value={metric.value} tone={metric.tone} prefix={"prefix" in metric ? metric.prefix : undefined} />
            </dd>
          </div>
        ))}
        {workspace.queue ? (
          <div className="flex min-w-0 items-baseline gap-1" title={`${workspace.queue.pending} messages queued by the agent harness`}>
            <dt className="order-2 text-xs text-content-tertiary">queued</dt>
            <dd className="text-xs font-medium"><Figure value={workspace.queue.pending} tone="pending" /></dd>
          </div>
        ) : null}
      </dl>
    </div>
  );
}
