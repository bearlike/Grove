/**
 * The shared duration vocabulary — used anywhere a session's time is shown,
 * not just the usage tree it was born in.
 *
 * A session carries at least two durations that must never collapse into one
 * column: `active_ms` is a WALL CLOCK (the union of the session's active
 * intervals, concurrency counted once) and `execution_ms` is COMPUTE (the same
 * intervals SUMMED across the root agent and every sub-agent), so ten
 * sub-agents running ten minutes side by side honestly read `10m` and `100m`.
 * That divergence is the measurement, not a double count — see
 * `DurationView` on the wire and design-system.md §3. Every surface that lists
 * sessions renders both, labelled "Wall clock" and "Compute", never one as the
 * other.
 */

/** What the page says when a number is absent. Never `0` — a fabricated zero
 * is a claim about spend that the store never made. */
export const NOT_MEASURED = "not measured";

/**
 * A measured duration in milliseconds, as "3h 12m" / "48m" / "24s".
 *
 * `null` is `NOT_MEASURED` and a measured zero is `0s`, which is the whole
 * reason this takes the nullable value rather than a pre-defaulted number: a
 * session Grove could not time and a session that did no work are different
 * facts, and this column is read as evidence of agent cost.
 *
 * Two units, largest first — the same rule `durationSince` states: a third unit
 * changes no decision and only churns the string. Seconds appear ONLY below a
 * minute, because these are session totals where "0h 0m" would read as broken.
 */
export function duration(ms: number | null | undefined): string {
  if (typeof ms !== "number" || !Number.isFinite(ms) || ms < 0) return NOT_MEASURED;
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest > 0 ? `${hours}h ${rest}m` : `${hours}h`;
}
