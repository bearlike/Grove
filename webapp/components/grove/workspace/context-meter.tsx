"use client";

import { Progress } from "@/components/ui/progress";
import { CardRegion } from "@/components/grove/card";
import { contextTone, formatContextWindow, type ContextTone } from "@/lib/grove/adapters/context";
import type { ContextWindowView } from "@/lib/grove/api";

/**
 * How full the model's context window is, as the harness itself reports it.
 *
 * The same meter idiom as `TicketProgressMeter` — a labelled row, the figure on
 * the right, a `Progress` underneath with `aria-valuenow` supplied at the call
 * site because the vendored bar never forwards `value` to its Radix root.
 *
 * **RENDERS NOTHING WHEN THE HARNESS SAID NOTHING, and that is the whole
 * decision.** `context` is `null` for a Claude Code session before its first
 * turn completes and for a Codex rollout older than the record that carries
 * the window; a meter drawn at 0 % there would claim an empty window on a
 * session that may be one turn from compaction. An absent meter is the honest
 * shape for an absent measurement, the rule the ticket rows already follow for
 * an unreported phase.
 *
 * The raw counts, rather than `used_fraction`, are the source for every visible
 * claim. That preserves reported overflow and gives Activity and the footer one
 * formatter instead of three separately rounded answers.
 */
/** The ramp, as the vendored bar's indicator class. Thresholds live in the adapter. */
const BAR_TONE: Record<ContextTone, string> = {
  info: "[&>[data-slot=progress-indicator]]:bg-info",
  success: "[&>[data-slot=progress-indicator]]:bg-success",
  warning: "[&>[data-slot=progress-indicator]]:bg-warning",
  destructive: "[&>[data-slot=progress-indicator]]:bg-destructive",
};

export function ContextMeter({
  context,
  unavailable,
}: {
  context: ContextWindowView | null | undefined;
  /** Why there is no window, when the daemon knows. See `FooterContext`. */
  unavailable?: "stale_native_worker" | null;
}) {
  const display = context ? formatContextWindow(context.used, context.size) : null;
  // A SUPPRESSED reading is a different fact from an unmeasured one, and only
  // one of them has a remedy. Rendering nothing for both is what made the fix
  // for the cumulative-usage bug look like the meter had simply disappeared.
  if (!display && unavailable === "stale_native_worker") {
    return (
      <CardRegion data-testid="context-meter-stale">
        <span className="text-xs text-content-secondary">Context window</span>
        <span className="text-sm text-warning">Not measured</span>
        <span className="text-xs text-content-tertiary">
          This agent started before the context fix and still reports cumulative
          usage rather than current occupancy. Respawn the workspace to measure it.
        </span>
      </CardRegion>
    );
  }
  if (!display) return null;
  const exact = `${display.exactCounts} tokens in the model's context window, as reported by the agent after its last request.`;
  // The track has one physical capacity. Capping its fill says it is over capacity
  // while the visible and announced percentage retain the reported overage.
  const visualPercent = Math.min(100, display.percentValue);
  const tone = BAR_TONE[contextTone(display.percentValue)];
  const percentText = `${display.percent}% used${display.percentValue > 100 ? " (over capacity)" : ""}`;

  return (
    <CardRegion data-testid="context-meter">
      <div className="flex min-w-0 items-baseline justify-between gap-2">
        <span className="min-w-0 text-xs text-content-secondary">Context window</span>
        <span
          className="shrink-0 text-sm font-normal tabular-nums text-content-primary"
          title={exact}
          data-testid="context-percent"
        >
          {display.percent}%
        </span>
      </div>
      <Progress
        // The vendored bar paints its indicator `bg-primary` and forwards no
        // indicator class, so the tone reaches it through the root's own
        // selector — the one seam that leaves the vendored file untouched.
        className={tone}
        value={visualPercent}
        aria-valuenow={visualPercent}
        aria-valuetext={percentText}
        aria-label="Context window used"
      />
      {/* ONE detail line. The exact figure used to print beneath this one and
          read as the same fact said twice; it lives in the percentage's
          tooltip, where a reader who wants the last digit goes anyway. */}
      <span className="text-xs tabular-nums text-content-tertiary" title={exact} data-testid="context-detail">
        {display.counts}
      </span>
    </CardRegion>
  );
}
