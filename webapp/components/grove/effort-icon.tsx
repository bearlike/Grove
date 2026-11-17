import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import {
  BrainIcon,
  SignalHighIcon,
  SignalIcon,
  SignalLowIcon,
  SignalMediumIcon,
  SignalZeroIcon,
  ZapIcon,
} from "lucide-react";

/**
 * One mark per reasoning-effort level, decided once for every picker.
 *
 * The signal bars are an ORDINAL ladder (more bars, more effort) and say
 * nothing about cost or token multipliers, which no provider publishes. A level
 * this table has never heard of keeps the control's own brain mark rather than
 * borrowing a neighbour's bars, because a wrong rung would read as a measured
 * position on a scale the provider never stated. The levels are the union of
 * what the adapters list today (`AgentSummaryView.efforts`).
 */
const LEVEL_ICON: Readonly<Record<string, LucideIcon>> = {
  minimal: SignalZeroIcon,
  low: SignalLowIcon,
  medium: SignalMediumIcon,
  high: SignalHighIcon,
  xhigh: SignalIcon,
  max: ZapIcon,
};

/** The mark for one effort level; the control's brain glyph when unrecognised. */
export function EffortMark({
  level,
  className,
}: {
  readonly level: string;
  readonly className?: string;
}): ReactNode {
  const Icon = LEVEL_ICON[level] ?? BrainIcon;
  return <Icon aria-hidden className={className} data-testid="effort-mark" data-level={level} />;
}
