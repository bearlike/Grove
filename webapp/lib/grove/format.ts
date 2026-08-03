/**
 * Compact human token counts for metric lines: `840`, `1.5k`, `2.3M`.
 *
 * One definition shared by every session metrics row (the dashboard's
 * SessionCard and the home page's ProjectSessions) so the two can't drift —
 * the thousands/millions thresholds and the one-decimal shape are a contract,
 * not a per-component choice.
 */
/**
 * A running duration, counted UP: `12s`, `1m 04s`, `6m 32s`, `1h 02m`.
 *
 * Distinct from `relativeTimeLabel`'s "3m ago" on purpose — that answers *how
 * stale is this*, and rounds hard because nobody cares about the seconds of a
 * timestamp. This answers *how long has this been running*, where the seconds
 * ARE the signal that something is still moving, so they stay visible for the
 * whole first hour. Zero-padded so the text never changes width and the row
 * can't jitter once a second.
 */
export function elapsedLabel(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes}m ${String(total % 60).padStart(2, "0")}s`;
  return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, "0")}m`;
}

export function humanTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}
