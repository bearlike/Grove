import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { AgentStateMark } from "@/components/shared/state-mark";
import { agentStateLabel } from "@/lib/grove/agent-state-tokens";
import type { AgentActivityState } from "@/lib/grove/types";

/**
 * Agent-state pill — the card's one state badge on the agent axis. Composes the
 * canonical `AgentStateMark` (the one glyph/color system) so the state reads the
 * same here as everywhere else: the hue lights only the glyph, the label stays
 * neutral foreground-muted text (color is never the only signal — a11y).
 *
 * Quiet-chrome (issue #96 deliverable D): every state wears the SAME neutral pill
 * — no filled-amber `blocked` treatment. Attention is carried on the card (left
 * accent bar / dot), not by shouting from this badge. `blocked` still reads
 * "action required" in words; the loudness is gone, the meaning isn't.
 *
 * Test seam: `data-testid="agent-state-badge"` + `data-state` + aria-label, the
 * visible label on `data-testid="agent-state-label"`, first child = the glyph.
 */
export function AgentStateBadge({
  state,
  className,
}: {
  state: AgentActivityState;
  className?: string;
}) {
  const label = state === "blocked" ? "action required" : agentStateLabel(state);
  return (
    <Badge
      variant="outline"
      data-testid="agent-state-badge"
      data-state={state}
      aria-label={`agent state: ${label}`}
      className={cn(
        "shrink-0 gap-1 whitespace-nowrap rounded-full border-transparent bg-muted/40 px-1.5 py-0 text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground",
        className,
      )}
    >
      <AgentStateMark state={state} />
      <span data-testid="agent-state-label">{label}</span>
    </Badge>
  );
}
