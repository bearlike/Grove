"use client";
import { useTheme } from "next-themes";
import { Separator } from "@/components/ui/separator";
import { statColor } from "@/lib/grove/status-tokens";
import { cn } from "@/lib/utils";

interface Props {
  ahead: number;
  behind: number;
  dirty: number;
  className?: string;
}

/**
 * Compact stat triplet. Vertical-stacked number + label, separated by
 * shadcn Separator (vertical) so dividers come from the design system.
 * Polarity-aware coloring is delegated to `statColor`; the component
 * stays presentational.
 */
export function StatTrio({ ahead, behind, dirty, className }: Props) {
  const { resolvedTheme } = useTheme();
  const dark = resolvedTheme === "dark";
  return (
    <div
      data-testid="stat-trio"
      className={cn("flex items-stretch gap-3", className)}
    >
      <Stat label="ahead" value={ahead} color={statColor("ahead", ahead, dark)} />
      <Separator orientation="vertical" className="h-auto" />
      <Stat label="behind" value={behind} color={statColor("behind", behind, dark)} />
      <Separator orientation="vertical" className="h-auto" />
      <Stat label="dirty" value={dirty} color={statColor("dirty", dirty, dark)} />
    </div>
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
    <div data-testid={`stat-${label}`} className="flex flex-col" style={{ color }}>
      <span className="text-base font-semibold tabular-nums leading-none">{value}</span>
      <span className="mt-0.5 text-[10px] uppercase tracking-wider opacity-90">
        {label}
      </span>
    </div>
  );
}
