import { cn } from "@/lib/utils";
import { phaseGlyph, phaseLabel } from "@/lib/grove/status-tokens";
import type { PhaseView } from "@/lib/grove/types";

/**
 * The task-phase axis — third axis alongside `AgentStateMark` (agent activity)
 * and `StatusBadge` (workspace lifecycle); see design-system.md +
 * `phase_palette.py` for why it renders differently from both.
 *
 * TWO registers of ONE axis, and the split is the whole design:
 *   - `PhaseBadge` (here) — the GLANCE. Dense list chrome (card header, rail
 *     meta row, the identity trigger) where a bar would not fit and does not
 *     belong.
 *   - `PhaseMeter` (`phase-meter.tsx`) — the READ. Detail surfaces with
 *     vertical room (the identity popover, the work panel's Info tab) where the
 *     axis can show what it actually is: progress through a fixed pipeline.
 * Both render NOTHING when `phase` is null — a workspace whose agent has never
 * reported a phase stays visually identical to before this axis existed
 * (design-system.md: "absence is not a state").
 *
 * They live in SEPARATE modules on purpose, not for tidiness. `PhaseBadge` is a
 * dependency-free leaf (`cn` + the status tokens) mounted by the session rail,
 * which the shared shell renders on EVERY route; `PhaseMeter` pulls the Radix
 * `Progress` primitive, which belongs only on the detail surfaces. One combined
 * module would drag that vendor dependency into the shell's module graph — dev
 * mode does not tree-shake, so the whole app pays its compile on first paint.
 * Keep this file free of imports the rail does not already need.
 */

/**
 * The compact readout. Deliberately NOT a `StatusBadge`-shaped pill: phase is
 * ORDERED, sequential data (the palette is a single-hue ramp, not six
 * unrelated hues), so a categorical pill would waste the one property that
 * makes this axis different. So this mirrors the `Stat` grammar instead: a
 * small glyph that is ITSELF a fill stage (○ ◔ ◑ ◕ ● ✓, so the shape carries
 * progress even in grayscale) + the bare `index+1/total` fraction, muted text,
 * tabular-nums — phase name + optional note live in the tooltip only, the same
 * "unit in the tooltip, not the row" rule `Stat` already keeps. It also costs a
 * fraction of a pill's width, which matters on the HEADER row (state mark +
 * title + relative time) and on the rail's already-dense meta line.
 *
 * There is deliberately no `showLabel` variant: the register that needs the
 * phase NAME is `PhaseMeter`, and one prop toggling between two registers
 * would blur the split above.
 */
export function PhaseBadge({
  phase,
  className,
}: {
  phase: PhaseView | null;
  className?: string;
}) {
  if (!phase) return null;
  const label = phaseLabel(phase.phase);
  const title = phase.note ? `${label} — ${phase.note}` : label;
  return (
    <span
      data-testid="phase-badge"
      data-phase={phase.phase}
      title={title}
      aria-label={`task phase: ${title}, step ${phase.index + 1} of ${phase.total}`}
      className={cn(
        "inline-flex shrink-0 items-center gap-1 whitespace-nowrap text-[11px] text-muted-foreground",
        className,
      )}
    >
      <span
        aria-hidden
        className="font-mono leading-none"
        style={{ color: `var(--phase-${phase.phase}, var(--phase-scoping))` }}
      >
        {phaseGlyph(phase.phase)}
      </span>
      <span className="tabular-nums">
        {phase.index + 1}/{phase.total}
      </span>
    </span>
  );
}
