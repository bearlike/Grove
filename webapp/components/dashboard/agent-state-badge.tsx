import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { agentStateGlyph, agentStateLabel } from "@/lib/grove/agent-state-tokens";
import type { AgentActivityState } from "@/lib/grove/types";

/**
 * Agent-state pill — the card's one state badge on the agent axis. Mirrors
 * StatusBadge's contract: the state hue lights only the glyph while the label
 * stays foreground text, so color is never the only signal (a11y) and label
 * contrast holds AA on both themes. BLOCKED reads "action required" and is the
 * loudest treatment on the wall (filled amber tint + accent border): it is the
 * one state where the agent provably burns wall-clock until a human answers.
 *
 * Test seam: `data-testid="agent-state-badge"` + `data-state` + aria-label,
 * with the visible label on `data-testid="agent-state-label"`.
 */
export function AgentStateBadge({
  state,
  className,
}: {
  state: AgentActivityState;
  className?: string;
}) {
  const blocked = state === "blocked";
  const label = blocked ? "action required" : agentStateLabel(state);
  return (
    <Badge
      variant="outline"
      data-testid="agent-state-badge"
      data-state={state}
      aria-label={`agent state: ${label}`}
      className={cn(
        "shrink-0 gap-1 whitespace-nowrap rounded-full px-2 py-0 text-[10px] font-medium uppercase tracking-wide text-foreground",
        blocked
          ? "border-[var(--agent-blocked)] bg-[var(--agent-blocked)]/15 font-semibold"
          : "bg-muted/60",
        className,
      )}
      // `?? unknown` fallback: a streamed delta can carry a state this client's
      // enum predates; an unset var would otherwise render an invisible glyph.
      style={{ ["--agent-c" as string]: `var(--agent-${state}, var(--agent-unknown))` }}
    >
      <span
        aria-hidden
        className={cn(
          "font-mono leading-none text-[var(--agent-c)]",
          state === "working" && "animate-grove-pulse motion-reduce:animate-none",
        )}
      >
        {agentStateGlyph(state)}
      </span>
      <span data-testid="agent-state-label">{label}</span>
    </Badge>
  );
}
