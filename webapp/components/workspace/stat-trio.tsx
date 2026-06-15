"use client";
import { useTheme } from "next-themes";
import { statColor } from "@/lib/grove/status-tokens";
import { cn } from "@/lib/utils";

interface Props {
  ahead: number;
  behind: number;
  dirty: number;
  className?: string;
}

/**
 * Compact diff-stat triplet (issue #96 deliverable C: pipes → middots). Three
 * inline `value AHEAD · value BEHIND · value DIRTY` stats, middot-separated to
 * match the rest of the card's quiet meta texture (the old vertical Separator
 * "pipes" read as loud chrome on a demoted footer line). The numerals carry the
 * polarity hue (`statColor` — green ahead, amber behind/dirty, muted at zero);
 * the unit labels stay hard-muted so color appears only where a count means
 * something. Presentational: all color policy is delegated to `statColor`.
 */
export function StatTrio({ ahead, behind, dirty, className }: Props) {
  const { resolvedTheme } = useTheme();
  const dark = resolvedTheme === "dark";
  return (
    <div
      data-testid="stat-trio"
      className={cn("flex items-center gap-1.5 text-muted-foreground", className)}
    >
      <Stat label="ahead" value={ahead} color={statColor("ahead", ahead, dark)} />
      <Middot />
      <Stat label="behind" value={behind} color={statColor("behind", behind, dark)} />
      <Middot />
      <Stat label="dirty" value={dirty} color={statColor("dirty", dirty, dark)} />
    </div>
  );
}

function Middot() {
  return (
    <span aria-hidden className="select-none text-muted-foreground/40">
      ·
    </span>
  );
}

function Stat({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color: string;
}) {
  return (
    <span data-testid={`stat-${label}`} className="inline-flex items-baseline gap-1">
      <span className="text-xs font-semibold tabular-nums leading-none" style={{ color }}>
        {value}
      </span>
      <span className="text-[10px] uppercase tracking-wider">{label}</span>
    </span>
  );
}
