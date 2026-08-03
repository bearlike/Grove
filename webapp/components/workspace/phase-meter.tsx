import type { CSSProperties } from "react";
import { Progress } from "@/components/ui/progress";
import { RelativeTime } from "@/components/shared/relative-time";
import { cn } from "@/lib/utils";
import { phaseGlyph, phaseLabel } from "@/lib/grove/status-tokens";
import type { PhaseView } from "@/lib/grove/types";

/**
 * The task-phase axis in its READ register — see `phase-badge.tsx` for the axis
 * as a whole and for why these are two modules rather than one (this file's
 * `Progress` dependency must not reach the always-mounted session rail).
 *
 * The expanded readout — the SAME axis, rendered as progress because that is
 * what `index`/`total` on the wire actually describe (six ordered steps,
 * scoping → done). A flat colored badge would throw the ordering away; a
 * determinate bar answers "how far along" pre-attentively, which is the
 * question an operator opens a workspace to ask.
 *
 * The bar is the shadcn/Radix `Progress` primitive verbatim (vendor-first —
 * never a bespoke meter): the only overrides are geometry (`h-1.5`, calm) and
 * the indicator hue, injected as a CSS var through the arbitrary-child variant
 * so `components/ui/progress.tsx` stays canonical and re-`shadcn add`-able.
 *
 * The bar is never the SOLE carrier: the glyph, the phase name, and the `n/6`
 * fraction all say the same thing in text, so the ramp's lighter early steps
 * (lime-400 on a light canvas) failing a 3:1 non-text read costs no
 * information — the contrast contract binds text, and every word here clears
 * it on `--muted-foreground`/`--foreground`.
 *
 * `updated_at` rides along because a phase is a CLAIM the agent last made, not
 * an observation: "verifying, reported 40 minutes ago" is the stalled-run tell,
 * and it is invisible on the compact badge.
 */
export function PhaseMeter({
  phase,
  className,
}: {
  phase: PhaseView | null;
  className?: string;
}) {
  if (!phase) return null;
  const label = phaseLabel(phase.phase);
  const step = phase.index + 1;
  const pct = Math.max(0, Math.min(100, (step / Math.max(1, phase.total)) * 100));
  return (
    <div
      data-testid="phase-meter"
      data-phase={phase.phase}
      className={cn("flex flex-col gap-1.5", className)}
    >
      <div className="flex items-baseline gap-1.5 text-xs">
        <span
          aria-hidden
          className="font-mono leading-none"
          style={{ color: `var(--phase-${phase.phase}, var(--phase-scoping))` }}
        >
          {phaseGlyph(phase.phase)}
        </span>
        <span className="font-medium text-foreground">{label}</span>
        <span className="ml-auto shrink-0 tabular-nums text-muted-foreground">
          {step}/{phase.total}
        </span>
      </div>
      {/* The ARIA is restated on the consumer, NOT patched into the primitive:
          shadcn's `Progress` destructures `value` for the indicator transform
          and never forwards it to the Radix Root, so the Root self-reports
          `indeterminate` and emits no `aria-valuenow`. Radix spreads incoming
          props AFTER its own aria-*, so these win — and they carry the honest
          6-step scale rather than the percentage the fill needs. Keeping the
          fix here leaves `components/ui/progress.tsx` byte-identical to the
          registry, so a future `shadcn add --overwrite` can't silently revert
          it. */}
      <Progress
        value={pct}
        aria-label={`task phase: ${label}`}
        aria-valuemin={0}
        aria-valuemax={phase.total}
        aria-valuenow={step}
        aria-valuetext={`step ${step} of ${phase.total}`}
        className="h-1.5 [&>div]:bg-[var(--phase-hue)]"
        style={
          { "--phase-hue": `var(--phase-${phase.phase}, var(--phase-scoping))` } as CSSProperties
        }
      />
      {phase.note && (
        <p data-testid="phase-note" className="text-xs leading-snug text-muted-foreground">
          {phase.note}
        </p>
      )}
      <p className="text-[11px] text-muted-foreground">
        reported <RelativeTime iso={phase.updated_at} />
      </p>
    </div>
  );
}
