import { cn } from "@/lib/utils";

/**
 * An icon + count span (issue #96 / the Devin session-row language): a small
 * lucide glyph with its numeral, where the numeral inherits the icon's color
 * (purple branch count, green PR count, …). These are lightweight inline spans,
 * NOT pill `Badge`s — that is what keeps a metadata row quiet. Pure + tiny;
 * pass the icon already sized by the caller. `color` is any CSS color (a
 * `var(--ref-*)` token, typically); omit it to inherit the surrounding text.
 */
export function CountChip({
  icon,
  count,
  color,
  label,
  className,
}: {
  icon: React.ReactNode;
  count: number;
  color?: string;
  /** Accessible name, e.g. "commits ahead" — the icon stays decorative. */
  label?: string;
  className?: string;
}) {
  return (
    <span
      data-testid="count-chip"
      className={cn("inline-flex items-center gap-1 tabular-nums", className)}
      style={color ? { color } : undefined}
      title={label}
    >
      <span aria-hidden className="inline-flex shrink-0 items-center">
        {icon}
      </span>
      <span>{count}</span>
      {label && <span className="sr-only">{label}</span>}
    </span>
  );
}
