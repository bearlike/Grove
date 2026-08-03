import type { LucideIcon } from "lucide-react";
import { humanTokens } from "@/lib/grove/format";

/**
 * The ONE stat atom — a lucide glyph + a compacted, tabular-nums
 * value at whatever meta tier the parent sets. THE single way any countable
 * metric renders on the workspace card: git provenance (ahead/behind/dirty) and
 * agent activity (turns/tool calls/tokens) all flow through it, so they speak
 * one grammar — icon size, icon↔value gap, tone rule — never two rival
 * dialects stacked on adjacent rows.
 *
 * The human unit lives in the `title`/aria-label ("<value> <label>"), never as a
 * cryptic sigil in the row. Neutral `text-muted-foreground`; `tone` colors the
 * VALUE only — green for the additive count (`--ref-add`, ahead), red for the
 * subtractive one (`--ref-remove`, behind).
 *
 * Zero-suppression is the atom's job, not the caller's: a null / zero value
 * renders nothing, so a clean workspace never shows "0 ahead 0 behind". Callers
 * pass every stat unconditionally and the atom drops the empty ones.
 *
 * Font-size is inherited (not fixed here) so the rail can speak the same grammar
 * at its own tier — sharing the atom while a surface differs in fields is the
 * point, not forcing one literal row.
 */
export type StatTone = "add" | "remove";

const TONE_VAR: Record<StatTone, string> = {
  add: "var(--ref-add)",
  remove: "var(--ref-remove)",
};

export function Stat({
  icon: Icon,
  value,
  label,
  tone,
}: {
  icon: LucideIcon;
  value: string | number | null | undefined;
  label: string;
  tone?: StatTone;
}) {
  // Zero-suppression on the raw value: 0 / null / undefined / "" all vanish.
  if (value == null || value === 0 || value === "") return null;
  // Compact any count to the token shape (18.9M, 160.3k). `humanTokens` is
  // identity below 1k, so ahead/behind/dirty/turns/tools pass through untouched
  // and only real token counts fold — one formatter, no per-stat branching.
  const display = typeof value === "number" ? humanTokens(value) : value;
  return (
    <span
      data-testid="stat"
      data-stat={label}
      title={`${display} ${label}`}
      aria-label={`${display} ${label}`}
      className="inline-flex items-center gap-1 whitespace-nowrap text-muted-foreground"
    >
      <Icon aria-hidden className="size-3 shrink-0" />
      <span className="tabular-nums" style={tone ? { color: TONE_VAR[tone] } : undefined}>
        {display}
      </span>
    </span>
  );
}
