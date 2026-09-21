"use client";

import {
  CircleDashedIcon,
  CirclePlayIcon,
  OctagonAlertIcon,
  TicketIcon,
  TriangleAlertIcon,
  WifiOffIcon,
  type LucideIcon,
} from "lucide-react";

import { Figure, Glyph, Section } from "@/components/grove/shell/footer/primitives";
import {
  fleetFigures,
  type FleetCounts,
  type FleetFigure,
  type FleetProgress,
} from "@/lib/grove/adapters/footer";

/** Each figure's glyph and tone. The tone table is `AGENT_TONE`'s, not a second opinion. */
const LOOK: Record<FleetFigure["key"], { Icon: LucideIcon; tone: string }> = {
  working: { Icon: CirclePlayIcon, tone: "text-success" },
  idle: { Icon: CircleDashedIcon, tone: "text-content-secondary" },
  // `destructive`, matching `AGENT_TONE.blocked` in fleet/tokens.ts. A blocked
  // agent is the same fact here as on its fleet card, and a second opinion
  // about its severity is how one surface starts under-reporting what
  // another calls urgent.
  blocked: { Icon: OctagonAlertIcon, tone: "text-destructive" },
  attention: { Icon: TriangleAlertIcon, tone: "text-warning" },
  tickets: { Icon: TicketIcon, tone: "text-content-secondary" },
};

/**
 * HOW THE FLEET IS DOING, said once.
 *
 * Replaces two sections that answered the same question three overlapping
 * ways — `1 working 0 idle 1 blocked`, `22 tickets 82% of 11 reported`,
 * `4 need you` — and spent a sentence on a denominator. Which figures appear
 * and how they are worded is decided in `fleetFigures`, where the tests are;
 * this only draws them.
 *
 * ONE GROUP, NOT A SEAM PER FIGURE. The figures are the parts of one answer,
 * so whitespace divides them; a rule between each was the "too many borders"
 * complaint in the neighbouring section. The denominator that keeps the
 * percentage honest rides each figure's tooltip: a qualification is one hover
 * away, not a headline.
 */
export function FleetSection({
  counts,
  progress,
  attention,
  connected,
}: {
  counts: FleetCounts;
  progress: FleetProgress;
  attention: number;
  connected: boolean;
}): React.ReactNode {
  if (!connected) {
    return (
      <Section id="footer-fleet" wash className="shrink-0">
        <span className="inline-flex items-center gap-1 px-1 text-warning">
          <Glyph Icon={WifiOffIcon} />
          fleet unavailable
        </span>
      </Section>
    );
  }
  const figures = fleetFigures(counts, progress, attention);
  if (figures.length === 0) return null;

  return (
    <Section id="footer-fleet" wash className="shrink-0">
      <span className="inline-flex shrink-0 items-center gap-2.5 px-1">
        {figures.map((figure) => (
          <Figure
            key={figure.key}
            Icon={LOOK[figure.key].Icon}
            tone={LOOK[figure.key].tone}
            count={figure.count}
            word={figure.word}
            suffix={figure.percent === null ? null : `${figure.percent}%`}
            title={figure.title}
            testId={`footer-fleet-${figure.key}`}
          />
        ))}
      </span>
    </Section>
  );
}
