"use client";

import { ClockIcon, FilePenLineIcon } from "lucide-react";

import { absoluteTime, relativeTime, useNow } from "@/components/grove/relative-time";
import { abbreviate } from "@/components/grove/usage/format";
import { cn } from "@/lib/utils";
import type { WorkspaceActivity } from "./types";

/** One semantic treatment for a figure, never an unrelated badge per metric. */
const METRIC_TONE = {
  added: "text-success",
  removed: "text-destructive",
  pending: "text-warning",
  neutral: "text-content-secondary",
  /** The rail's file count: a size beside its glyph, not an alarm (see `SessionMetadata`). */
  count: "text-content-primary",
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
 * Keep quantities intact when enlarged text exceeds the compact ledger width.
 * Context is supplied by the row; creation age is not its activity sort time.
 *
 * The dirty count is NEUTRAL here and amber on the fleet card. The rail lists
 * every workspace at once, and nearly every live one has uncommitted files, so
 * amber on each row was a column of warnings saying nothing about any one row;
 * the diff figures beside it keep their hues because they have a direction.
 */
export function SessionMetadata({
  workspace,
  context,
}: {
  workspace: WorkspaceActivity;
  context?: React.ReactNode;
}): React.ReactNode {
  const { dirty_files: dirty, diff_added: added, diff_removed: removed } = workspace;
  const noChanges = dirty === 0 && added === 0 && removed === 0;
  return (
    <div className="flex min-w-0 flex-col gap-1.5" data-testid="rail-metadata">
      {context}
      <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
        <span className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2.5 gap-y-1" data-testid="rail-ledger">
          {!noChanges ? (
            <>
            <MetricCell
              Icon={FilePenLineIcon}
              value={dirty}
              tone="count"
              detail={countLabel(dirty, "uncommitted files in the worktree")}
              testid="rail-dirty"
            />
            <MetricCell
              value={added}
              tone="added"
              prefix="+"
              detail={countLabel(added, "lines added on the branch since its diff base")}
              testid="rail-added"
            />
            <MetricCell
              value={removed}
              tone="removed"
              prefix="−"
              detail={countLabel(removed, "lines removed on the branch since its diff base")}
              testid="rail-removed"
            />
            </>
          ) : null}
        </span>
        <CreatedAge iso={workspace.state.created_at} />
      </div>
    </div>
  );
}

/** The row's single hover/focus tooltip uses the same labels as its figures. */
export function SessionMetricDetails({ workspace }: { workspace: WorkspaceActivity }): React.ReactNode {
  return (
    <>
      <p>{countLabel(workspace.dirty_files, "uncommitted files in the worktree")}</p>
      <p>{countLabel(workspace.diff_added, "lines added on the branch since its diff base")}</p>
      <p>{countLabel(workspace.diff_removed, "lines removed on the branch since its diff base")}</p>
    </>
  );
}

/** "7 uncommitted files in the worktree", or the honest unknown. */
function countLabel(value: number | null, detail: string): string {
  if (value === null) return `An unknown number of ${detail}`;
  return `${value.toLocaleString("en-US")} ${detail}`;
}

/**
 * One measurement, at exactly the width its value needs.
 *
 * `shrink-0` AND NO `LoopingText`, both deliberate. A figure is compared, not
 * read: clipping or animating it would change the value being compared. The exact
 * figure's exact value and scope are available in the row's native tooltip.
 */
function MetricCell({
  Icon,
  value,
  tone,
  prefix,
  detail,
  testid,
}: {
  Icon?: React.ComponentType<React.SVGProps<SVGSVGElement>>;
  value: number | null;
  tone: keyof typeof METRIC_TONE;
  prefix?: string;
  detail: string;
  testid: string;
}): React.ReactNode {
  return (
    <span className="flex shrink-0 items-center gap-1 whitespace-nowrap" aria-label={detail} data-testid={testid}>
      {Icon ? <Icon aria-hidden className="size-3.5 shrink-0 text-content-tertiary" /> : null}
      <span className={cn("tabular-nums", figureTone(value, tone))}>
        {figureText(value, prefix)}
      </span>
    </span>
  );
}

/** Unknown and zero are both quiet; only a measurement that says something is toned. */
function figureTone(value: number | null, tone: keyof typeof METRIC_TONE): string {
  return value === null || value === 0 ? "text-content-tertiary" : METRIC_TONE[tone];
}

function figureText(value: number | null, prefix = ""): string {
  return value === null ? "—" : `${prefix}${abbreviate(value)}`;
}

/**
 * When the workspace was CREATED — asked for by name, and not the value the
 * rail orders by.
 *
 * The rail orders by activity but labels this value as creation, and the row
 * tooltip names both instants. It uses the shared clock and compact age formatter;
 * the pre-mount absolute time is capped until hydration replaces it.
 */
function CreatedAge({ iso }: { iso: string }): React.ReactNode {
  const now = useNow();
  const exact = absoluteTime(iso);
  const label = `Created ${exact}`;
  return (
    <span className="flex shrink-0 items-center gap-1 whitespace-nowrap text-content-tertiary" aria-label={label} data-testid="rail-created">
      <ClockIcon aria-hidden className="size-3.5 shrink-0" />
      <span className="sr-only">Created: </span>
      <span className={cn("tabular-nums", now === null && "max-w-16 truncate")}>
        {now === null ? exact : relativeTime(iso, now)}
      </span>
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
