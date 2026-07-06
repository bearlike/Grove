import { cn } from "@/lib/utils";
import { agentStateGlyph, agentStateLabel } from "@/lib/grove/agent-state-tokens";
import type { AgentActivityState } from "@/lib/grove/types";

/**
 * The canonical agent-state mark (issue #96 deliverable C) — THE one glyph/color
 * system for agent state, reused on every surface (card, status pill, detail
 * context bar) so a state never reads two different ways. The hue lights only
 * the glyph (`--agent-*`, mirrored from Python); `working` pulses like a
 * terminal cursor. Pure + tiny: a colored, optionally-pulsing glyph span.
 *
 * Color is an enhancement, never the only signal — pair it with the label
 * (`agentStateLabel`) or a `title` wherever it stands alone.
 *
 * `?? unknown` fallback: a streamed delta can carry a state this client's enum
 * predates; an unset var would render an invisible glyph, so resolve to neutral.
 */
export function AgentStateMark({
  state,
  className,
}: {
  state: AgentActivityState;
  className?: string;
}) {
  return (
    <span
      aria-hidden
      data-testid="state-mark"
      data-state={state}
      title={agentStateLabel(state)}
      className={cn(
        // `transition-colors` cross-fades the hue when the agent state flips
        // (working→waiting→idle …) instead of snapping — the glyph char still
        // swaps instantly, but the color eases (design-direction.md §5).
        "inline-block font-mono leading-none transition-colors duration-200",
        state === "working" && "animate-grove-pulse motion-reduce:animate-none",
        className,
      )}
      style={{ color: `var(--agent-${state}, var(--agent-unknown))` }}
    >
      {agentStateGlyph(state)}
    </span>
  );
}
