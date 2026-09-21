"use client";

/**
 * Port of elements/tool-timeline.tsx. Deltas: compose native tool parts instead
 * of inert rows; accept icon nodes and a summary icon set; use the app's type
 * ramp instead of pixel literals. The upstream component exposes no row/body
 * or trigger slots, so request/response disclosure cannot be composed into it.
 */
import { ChevronRightIcon } from "lucide-react";
import type { CSSProperties, PropsWithChildren, ReactNode } from "react";
import { CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { ToolGroupRoot } from "@/components/assistant-ui/tool-group";
import { collapsePanel, ShimmerLabel } from "@/components/elements/surfaces";
import { ToolFallbackRoot, ToolFallbackContent } from "@/components/assistant-ui/tool-fallback";
import { AppIcon } from "@/components/grove/app-icon";
import { iconTint } from "@/lib/grove/adapters/icon-tint";
import { cn } from "@/lib/utils";
import type { ToolTimelineStat } from "@/lib/grove/adapters/tool-catalog";

export function ToolTimeline({
  open, onOpenChange, streaming, label, icons, stats, status, children,
}: PropsWithChildren<{
  open: boolean;
  onOpenChange: (open: boolean) => void;
  streaming: boolean;
  label: string;
  icons: readonly string[];
  /** Per-file totals for the run, drawn under the steps — upstream's `stats`. */
  stats: readonly ToolTimelineStat[];
  status: ReactNode;
}>): ReactNode {
  return (
    <ToolGroupRoot variant="ghost" data-slot="tool-timeline" data-testid="tool-call-group" open={open} onOpenChange={onOpenChange} className="w-full min-w-0">
      <CollapsibleTrigger className="group/trigger text-content-tertiary hover:text-content-primary flex max-w-full flex-wrap items-center gap-1.5 rounded-md py-1 text-sm transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring">
        <ChevronRightIcon aria-hidden className="size-3.5 shrink-0 opacity-60 transition-transform duration-200 ease-[cubic-bezier(0.32,0.72,0,1)] group-data-open/trigger:rotate-90 group-data-panel-open/trigger:rotate-90 motion-reduce:transition-none" />
        {/* The stack stays MOUNTED when the run opens and recedes instead. It
            used to unmount, which moved the label leftward by the pill's whole
            width the instant you clicked — the row you were aiming at jumped
            out from under the pointer. Open is a quieter state of the same
            object, not a different row. */}
        {icons.length > 0 ? <ToolIconStack icons={icons} open={open} /> : null}
        {/* ONE layer, not a crossfade between a string and itself. `SwapLabel`
            animates BETWEEN two different labels; this timeline's summary text
            is the same string in both slots, so it was paying a permanent
            `blur(2px)` filter on a hidden duplicate for a transition whose two
            endpoints are identical — measured on the deployed app as 78 blurred
            layers across 78 groups. Shimmer still marks the streaming state,
            and it only mounts while this group is actually live. */}
        <ShimmerLabel
          active={streaming}
          className="relative inline-block text-start leading-none tabular-nums"
        >
          {label}
        </ShimmerLabel>
        {status}
      </CollapsibleTrigger>
      <CollapsibleContent className={cn(collapsePanel, "outline-none")}>
        <div className="flex min-w-0 flex-col gap-1.5 ps-2 py-1">
          {children}
          {/* Upstream's `stats` row, kept: one chip per file the run changed,
              summing every edit to it. It answers "what did this run touch"
              without reopening each step, and it is deliberately INSIDE the
              collapsible — a reader who has not opened the run already has the
              count in the label. */}
          {stats.length > 0 ? (
            <div className="flex flex-wrap gap-1.5 pt-1" data-testid="tool-timeline-stats">
              {stats.map(stat => (
                <span
                  key={stat.file}
                  className="bg-foreground/[0.06] text-content-secondary inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 font-mono text-xs tabular-nums"
                >
                  <span className="truncate">{stat.file}</span>
                  <span className="text-success">+{stat.added}</span>
                  <span className="text-destructive">−{stat.removed}</span>
                </span>
              ))}
            </div>
          ) : null}
        </div>
      </CollapsibleContent>
    </ToolGroupRoot>
  );
}

// Limit the summary, not the timeline's steps. Overflow remains explicit.
const MAX_STACK = 5;

function ToolIconStack({ icons, open }: { icons: readonly string[]; open: boolean }): ReactNode {
  const shown = icons.slice(0, MAX_STACK);
  const overflow = icons.length - shown.length;
  return (
    <span className="tool-icon-stack flex shrink-0 items-center" data-state={open ? "open" : "closed"} data-testid="tool-timeline-icons" aria-hidden>
      <span className="tool-icon-stack-row flex items-center">
        {shown.map(slug => (
          <span
            key={slug}
            className="tool-icon-stack-disc flex items-center justify-center"
            style={{ "--tool-stack-tint": iconTint(slug) ?? undefined } as CSSProperties}
          >
            <AppIcon slug={slug} className="size-3.5 shrink-0" />
          </span>
        ))}
        {overflow > 0 ? (
          <span className="tool-icon-stack-disc tool-icon-stack-more flex items-center justify-center tabular-nums">+{overflow}</span>
        ) : null}
      </span>
    </span>
  );
}

export function ToolTimelineStep({
  verb, chip, summary, icon, running, metadata, status, callId, name, onOpenChange, children,
}: PropsWithChildren<{
  verb: string;
  chip: string;
  summary: string;
  icon: string;
  running: boolean;
  metadata: ReactNode;
  status: string;
  callId?: string;
  name: string;
  /** Observes the disclosure without controlling it: a withheld tool body is
   * fetched when a reader opens this step, and the caller cannot see the
   * vendored root's own uncontrolled state. */
  onOpenChange?: (open: boolean) => void;
}>): ReactNode {
  return (
    <ToolFallbackRoot data-testid="tool-call" data-tool-status={status} data-tool-use-id={callId} onOpenChange={onOpenChange}>
      <CollapsibleTrigger
        data-slot="tool-timeline-step"
        title={`${name}: ${chip}`}
        className="group/trigger text-content-tertiary hover:text-content-primary flex w-full min-w-0 items-center gap-2 rounded-md py-1 text-sm transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <AppIcon slug={icon} className="size-5 shrink-0" />
        <ShimmerLabel active={running} className="relative inline-block shrink-0 leading-none">{verb}</ShimmerLabel>
        <span title={chip} data-testid="tool-target-summary" className="bg-foreground/[0.06] text-content-secondary min-w-0 max-w-64 truncate rounded-md px-1.5 py-0.5 font-mono text-xs">{summary}</span>
        <span className="flex shrink-0 items-center gap-1.5">{metadata}</span>
        <ChevronRightIcon aria-hidden className="size-3 shrink-0 transition-transform group-data-open/trigger:rotate-90 group-data-panel-open/trigger:rotate-90 motion-reduce:transition-none" />
      </CollapsibleTrigger>
      <ToolFallbackContent className="[&>div]:ps-3 [&>div]:pt-0.5 [&>div]:pb-1">{children}</ToolFallbackContent>
    </ToolFallbackRoot>
  );
}
