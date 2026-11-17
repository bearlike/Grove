"use client";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { CardShell } from "@/components/grove/card";
import { cn } from "@/lib/utils";
import { columnPresentation } from "./fleet-board";
import type { FleetProgress } from "./kanban";

/**
 * The board's reading, before the board: how much of the visible fleet is
 * finished, how much is moving, and what needs a person.
 *
 * The figures are over the VISIBLE rows, so a filter reframes them — that is
 * the point of filtering a factory floor: "of the Grove workspaces, 2 of 9
 * are done". Each figure carries its word (§4.7: a count alone is colour-only)
 * and the segmented bar carries one segment per column in the board's own
 * order, so the bar is the board compressed to one line.
 */
export function FleetProgressStrip({ progress }: { progress: FleetProgress }): React.ReactNode {
  const percent = progress.fraction === null ? null : Math.round(progress.fraction * 100);
  return (
    <CardShell className="px-3 py-2" data-testid="fleet-progress">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
        <Figure label="Complete" value={percent === null ? "—" : `${percent}%`} detail={`${progress.done} of ${progress.total} handed off`} tone={progress.fraction === 1 ? "success" : undefined} testId="fleet-progress-complete" />
        <Figure label="In flight" value={String(progress.inFlight)} detail="claiming a phase before handoff" testId="fleet-progress-in-flight" />
        <Figure label="Unreported" value={String(progress.unreported)} detail="no phase claim yet" testId="fleet-progress-unreported" />
        <Figure label="Blocked" value={String(progress.blocked)} detail="cannot finish as reported" tone={progress.blocked > 0 ? "warning" : undefined} testId="fleet-progress-blocked" />
        <Figure label="Needs attention" value={String(progress.attention)} detail="waiting on a person right now" tone={progress.attention > 0 ? "destructive" : undefined} testId="fleet-progress-attention" />
        <PhaseBar progress={progress} />
      </div>
    </CardShell>
  );
}

function Figure({
  label,
  value,
  detail,
  tone,
  testId,
}: {
  label: string;
  value: string;
  detail: string;
  tone?: "success" | "warning" | "destructive";
  testId: string;
}): React.ReactNode {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div className="flex min-w-0 flex-col" data-testid={testId} tabIndex={0}>
          <span className="text-xs text-content-tertiary">{label}</span>
          <span
            className={cn(
              "text-lg font-semibold tabular-nums text-content-primary",
              tone === "success" && "text-success",
              tone === "warning" && "text-warning",
              tone === "destructive" && "text-destructive",
            )}
          >
            {value}
          </span>
        </div>
      </TooltipTrigger>
      <TooltipContent>{detail}</TooltipContent>
    </Tooltip>
  );
}

/**
 * One segment per column, widths proportional to count, in board order. The
 * unreported segment is hatched by tone rather than filled so "nothing
 * claimed" never reads as progress of any kind.
 */
function PhaseBar({ progress }: { progress: FleetProgress }): React.ReactNode {
  if (progress.total === 0) return null;
  return (
    <div
      className="fleet-phase-bar flex h-2 min-w-48 flex-1 overflow-hidden bg-surface-sunken"
      role="img"
      aria-label={progress.byColumn
        .filter(({ count }) => count > 0)
        .map(({ key, count }) => `${columnPresentation(key).label} ${count}`)
        .join(", ")}
      data-testid="fleet-progress-bar"
    >
      {progress.byColumn
        .filter(({ count }) => count > 0)
        .map(({ key, count }) => (
          <Tooltip key={key}>
            <TooltipTrigger asChild>
              <span
                className="h-full"
                style={{ flexGrow: count }}
                data-segment={key}
                data-column={key}
              />
            </TooltipTrigger>
            <TooltipContent>
              {columnPresentation(key).label} · {count}
            </TooltipContent>
          </Tooltip>
        ))}
    </div>
  );
}
