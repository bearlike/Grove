import { cn } from "@/lib/utils";
import { runtimeGlyph, runtimeLabel } from "@/lib/grove/runtime-tokens";
import type { Runtime } from "@/lib/grove/types";

/**
 * The canonical runtime mark — the dense register of the isolation axis, the
 * twin of `AgentStateMark` for a different question: not "what is the agent
 * doing" but "what can it reach". One colored glyph, shared verbatim with the
 * TUI (`grove.core.contracts.runtime_palette`), so the wall, the rail and a
 * terminal all say the same thing with the same character.
 *
 * Two registers, like `StateDot` vs `AgentStateMark`: this bare mark on the
 * dense surfaces (card meta row, rail row), the labeled `RuntimeBadge` on the
 * identity surfaces that have room for a word.
 *
 * ALWAYS renders — there is no absent state on this axis (see runtime-tokens).
 * Color is an enhancement; the glyph and the `title`/`aria-label` carry it.
 */
export function RuntimeMark({
  runtime,
  className,
}: {
  runtime: Runtime;
  className?: string;
}) {
  return (
    <span
      data-testid="runtime-mark"
      data-runtime={runtime}
      title={`Runs on the ${runtimeLabel(runtime)}`}
      aria-label={`runtime: ${runtimeLabel(runtime)}`}
      role="img"
      className={cn("inline-block font-mono leading-none", className)}
      style={{ color: `var(--runtime-${runtime}, var(--runtime-host))` }}
    >
      {runtimeGlyph(runtime)}
    </span>
  );
}
