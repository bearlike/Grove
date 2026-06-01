"use client";
import Link from "next/link";
import { Bot, GitBranch } from "lucide-react";
import { Card } from "@/components/ui/card";
import { StatusBadge } from "./status-badge";
import { StatTrio } from "./stat-trio";
import { RelativeTime } from "@/components/shared/relative-time";
import type { WorkspaceCardModel } from "@/lib/grove/workspace-card";
import type { WorkspacePeekView } from "@/lib/grove/types";
import { cn } from "@/lib/utils";

const STATUS_VAR: Record<string, string> = {
  active: "var(--status-active)",
  running: "var(--status-running)",
  idle: "var(--status-idle)",
  offline: "var(--status-offline)",
  paused: "var(--status-paused)",
  orphaned: "var(--status-orphaned)",
  error: "var(--status-error)",
};

/**
 * One workspace, one card. Three vertical sections (header / identity /
 * footer); `flex h-full flex-col` + the parent grid's `auto-rows-fr`
 * pin every card in a row to the same height regardless of branch-name
 * length. Top accent border is the status-color cue you can scan in a
 * grid view (lazygit-style focus assertion adapted to per-card chrome).
 */
export function WorkspaceCard({
  model,
  peek,
}: {
  model: WorkspaceCardModel;
  peek: WorkspacePeekView | undefined;
}) {
  const { state } = model;
  const accent = STATUS_VAR[state.status] ?? "var(--status-offline)";

  return (
    <Link
      href={{ pathname: `/w/${state.id}` }}
      className="group block rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
      data-testid="workspace-card"
      data-status={state.status}
    >
      <Card
        className={cn(
          "relative flex h-full flex-col overflow-hidden border-t-2 bg-card text-card-foreground transition-[transform,box-shadow,border-color] duration-200",
          "group-hover:-translate-y-[1px] group-hover:shadow-md group-hover:border-t-[3px]",
        )}
        style={{ borderTopColor: accent }}
      >
        {/* Header: title + status badge */}
        <div className="flex items-start justify-between gap-3 p-4 pb-2">
          <h3
            title={state.title}
            className="min-w-0 flex-1 truncate text-base font-semibold leading-tight"
          >
            {state.title}
          </h3>
          <StatusBadge status={state.status} size="sm" className="shrink-0" />
        </div>

        {/* Identity: branch + agent. Always two lines so cards are uniform. */}
        <div className="space-y-1.5 px-4 pb-3 text-xs text-muted-foreground">
          <div className="flex min-w-0 items-center gap-1.5">
            <GitBranch aria-hidden className="h-3 w-3 shrink-0 text-[var(--ref-branch)]" />
            <span
              title={state.branch}
              className="truncate font-mono text-[var(--ref-branch)]"
            >
              {state.branch}
            </span>
            <span aria-hidden className="shrink-0">→</span>
            <span title={state.base_branch} className="shrink-0 truncate font-mono">
              {state.base_branch}
            </span>
          </div>
          <div className="flex min-w-0 items-center gap-1.5">
            <Bot aria-hidden className="h-3 w-3 shrink-0 text-[var(--ref-info)]" />
            <span title={state.agent_name} className="truncate font-mono text-[var(--ref-info)]">
              {state.agent_name}
            </span>
          </div>
        </div>

        {/* Footer pinned to bottom: stats + relative time. */}
        <div className="mt-auto flex items-center justify-between gap-2 border-t border-border bg-muted/40 px-4 py-2.5 text-xs">
          {peek ? (
            <StatTrio ahead={peek.base_ahead} behind={peek.base_behind} dirty={peek.dirty_files} />
          ) : (
            <StatTrioPlaceholder />
          )}
          <span className="shrink-0 text-muted-foreground">
            <RelativeTime iso={state.updated_at} />
          </span>
        </div>
      </Card>
    </Link>
  );
}

function StatTrioPlaceholder() {
  return (
    <div
      aria-hidden
      className="flex items-stretch gap-3 text-muted-foreground/70"
    >
      {(["ahead", "behind", "dirty"] as const).map((label, i, arr) => (
        <div key={label} className="flex items-stretch gap-3">
          <div className="flex flex-col">
            <span className="text-base font-semibold tabular-nums leading-none">—</span>
            <span className="mt-0.5 text-[10px] uppercase tracking-wider opacity-90">{label}</span>
          </div>
          {i < arr.length - 1 && <span aria-hidden className="w-px self-stretch bg-border" />}
        </div>
      ))}
    </div>
  );
}
