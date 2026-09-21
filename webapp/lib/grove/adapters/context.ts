/**
 * How full a context window is, as a semantic tier: the ONE place the ramp's
 * thresholds live.
 *
 * Four bands, not three, because the quiet end matters — `success` at 5% would
 * say "good" about a window that is merely empty, and the reading's job is to
 * show headroom draining. The 80 and 100 steps are the quota ramp's own upper steps, so a
 * quota and a context window turn amber and red at the same moments.
 *
 * Returns a NAME rather than a class because its two consumers paint different
 * things: the Activity meter tones a vendored bar's indicator through a child
 * selector, the footer tones its percentage text. A shared class string would
 * fit one and have to be rewritten by the other, which is how two surfaces come
 * to disagree about what 80% looks like.
 */
export type ContextTone = "info" | "success" | "warning" | "destructive";

export function contextTone(percent: number): ContextTone {
  if (percent >= 100) return "destructive";
  if (percent >= 80) return "warning";
  if (percent >= 50) return "success";
  return "info";
}

/** A context window's readable figures, all derived from the same reported counts. */
export type ContextWindowDisplay = {
  readonly used: string;
  readonly size: string;
  readonly counts: string;
  readonly exactCounts: string;
  readonly percent: string;
  /**
   * The same reading as a WHOLE percent, for the status band.
   *
   * Not a `precision` prop and not a second formatter: one derivation, two
   * spellings of it, so the band and the Activity card can never disagree
   * about the number itself. The card keeps `percent` — §3's precision rule
   * grants context occupancy two fractional digits on a surface opened to read
   * one thing, and the band is the opposite act, a glance where `28.95%` is
   * six characters of a figure nobody compares to a hundredth.
   */
  readonly percentWhole: string;
  readonly percentValue: number;
};

const EXACT = new Intl.NumberFormat("en-US");
const COMPACT = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const UNITS = [
  { divisor: 1_000_000_000, suffix: "B" },
  { divisor: 1_000_000, suffix: "M" },
  { divisor: 1_000, suffix: "K" },
] as const;
const PERCENT_SCALE = 10_000;

/**
 * Formats the provider's reported context counts without inventing a fraction.
 *
 * `used_fraction` is intentionally not an input. One raw numerator and
 * denominator produce the compact figure, exact figure, percent, and progress
 * value, so a stale derived field cannot make two visible claims disagree.
 */
export function formatContextWindow(
  used: number | null | undefined,
  size: number | null | undefined,
): ContextWindowDisplay | null {
  if (!isReportedCount(used) || !isReportedCount(size) || size === 0) return null;

  // Token counts are non-negative safe integers, so this stays finite and the
  // display rounds a non-negative hundredth with JavaScript's half-up rule.
  const percentValue = Math.round((used * PERCENT_SCALE) / size) / 100;
  const percent = String(percentValue);
  const formattedUsed = formatCount(used);
  const formattedSize = formatCount(size);

  return {
    used: formattedUsed,
    size: formattedSize,
    counts: `${formattedUsed} / ${formattedSize}`,
    exactCounts: `${EXACT.format(used)} / ${EXACT.format(size)}`,
    percent,
    // Rounded from the same `percentValue` every tone threshold reads, so the
    // band can never print a figure the ramp disagrees with.
    percentWhole: String(Math.round(percentValue)),
    percentValue,
  };
}

function isReportedCount(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function formatCount(value: number): string {
  const unitIndex = UNITS.findIndex(({ divisor }) => value >= divisor);
  if (unitIndex === -1) return EXACT.format(value);

  const unit = UNITS[unitIndex]!;
  const compact = COMPACT.format(value / unit.divisor);
  const nextUnit = UNITS[unitIndex - 1];
  return compact === "1,000" && nextUnit
    ? `${COMPACT.format(value / nextUnit.divisor)}${nextUnit.suffix}`
    : `${compact}${unit.suffix}`;
}
