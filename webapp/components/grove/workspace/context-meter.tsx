"use client";

import { Progress } from "@/components/ui/progress";
import { CardRegion } from "@/components/grove/card";
import type { ContextWindowView } from "@/lib/grove/api";

const COUNT = new Intl.NumberFormat("en-US");

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
 * `used_fraction` is carried on the wire so every surface rounds one way; the
 * two counts beside it are for the exact figure a reader hovers for.
 */
export function ContextMeter({ context }: { context: ContextWindowView | null | undefined }) {
  if (!context) return null;
  const percent = Math.round(context.used_fraction * 100);
  const exact = `${COUNT.format(context.used)} of ${COUNT.format(context.size)} tokens in the model's context window, as reported by the agent after its last request.`;

  return (
    <CardRegion data-testid="context-meter">
      <div className="flex min-w-0 items-baseline justify-between gap-2">
        <span className="min-w-0 text-xs text-content-secondary">Context window</span>
        <span
          className="shrink-0 text-sm font-medium tabular-nums text-content-primary"
          title={exact}
          data-testid="context-percent"
        >
          {percent}%
        </span>
      </div>
      <Progress
        value={percent}
        aria-valuenow={percent}
        aria-valuetext={`${percent}% used`}
        aria-label="Context window used"
      />
      <span className="text-xs text-content-tertiary" data-testid="context-detail">
        {COUNT.format(context.used)} / {COUNT.format(context.size)} tokens
      </span>
    </CardRegion>
  );
}
