/**
 * Compact human token counts for metric lines: `840`, `1.5k`, `2.3M`.
 *
 * One definition shared by every session metrics row (the dashboard's
 * SessionCard and the home page's ProjectSessions) so the two can't drift —
 * the thousands/millions thresholds and the one-decimal shape are a contract,
 * not a per-component choice.
 */
export function humanTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}
