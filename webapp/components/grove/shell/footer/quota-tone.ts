import type { QuotaTone } from "@/lib/grove/adapters/footer";

/**
 * The quota ramp as TEXT tone — the strip, the popover and the phone sheet.
 *
 * Thresholds live in `adapters/footer.ts` (`quotaTone`), where they are pure
 * and tested; this is only how those three surfaces paint them. One table, so
 * 50% cannot be amber in the band and calm in the sheet — which is the split
 * that produced the previous `accountTone`, a second threshold table that had
 * already drifted from this one by 30 percentage points.
 *
 * `warning` is the theme's existing amber (hue 75), not a new yellow: it is
 * already what every other at-risk reading in this app uses, and a second one
 * would be a second opinion about what at-risk looks like.
 */
export const QUOTA_TEXT_TONE: Record<QuotaTone, string> = {
  success: "text-success",
  warning: "text-warning",
  destructive: "text-destructive",
};
