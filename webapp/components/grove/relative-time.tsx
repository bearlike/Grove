"use client";

import { useEffect, useState } from "react";
import { TimerIcon } from "lucide-react";

/**
 * "2h ago", with the exact timestamp on hover.
 *
 * WHY A COMPONENT AND NOT A FORMATTER. Relative time reads from the clock, and
 * the clock is the one input a server and a browser never agree on. Rendering
 * it as a plain string produces a hydration mismatch on every single one of
 * these — the same class of bug as the vendored sidebar skeleton's
 * `Math.random()` width, which this app already had to work around once. So the
 * server renders the ABSOLUTE time (stable, correct, useful on its own) and the
 * relative form replaces it after mount. There is no flash of wrong content:
 * absolute → relative is strictly added information.
 *
 * WHY THE TOOLTIP IS THE EXACT VALUE. Same rule the usage page follows for
 * abbreviated numbers — an approximation is the reading affordance and the
 * precise value is always one hover away, never destroyed. "3d ago" is what you
 * scan; "Aug 8, 2026, 4:12:07 PM" is what you quote in a bug report.
 *
 * `<time dateTime>` rather than a `<span>`: the machine-readable instant
 * survives regardless of which of the two forms is currently painted.
 */

const MINUTE = 60;
const HOUR = MINUTE * 60;
const DAY = HOUR * 24;
/** Mean Gregorian month and year, in seconds.
 *
 * Approximate on purpose. A calendar-exact "2 months 5 days ago" would need the
 * civil date arithmetic of both instants, and at the scale these appear — a
 * workspace someone is deciding whether to keep — nobody acts differently on the
 * two-day difference a leap year makes. The approximation is stated here rather
 * than hidden so nobody later reads these constants as a bug. */
const MONTH = 30.436875 * DAY;
const YEAR = 365.2425 * DAY;

/**
 * Whole units only, largest that fits, no "about".
 *
 * A fleet row is scanned, not read, so "2h" beats "about 2 hours" and a
 * fractional unit is noise at every scale that matters here. Future instants
 * clamp to "just now" rather than rendering "in 3s": a workspace whose
 * `created_at` is a second ahead of this browser's clock is a clock-skew
 * artifact, not a scheduled event, and Grove has no future timestamps to show.
 */
export function relativeTime(iso: string, now: number): string {
  const seconds = Math.round((now - new Date(iso).getTime()) / 1000);
  if (!Number.isFinite(seconds) || seconds < MINUTE) return "just now";
  if (seconds < HOUR) return `${Math.floor(seconds / MINUTE)}m ago`;
  if (seconds < DAY) return `${Math.floor(seconds / HOUR)}h ago`;
  return `${Math.floor(seconds / DAY)}d ago`;
}

/**
 * The same age, at the precision a DETAIL surface reads — "2h 14m ago",
 * "3d 4h ago", "2mo 5d ago", "1y 3mo ago".
 *
 * WHY THIS EXISTS BESIDE `relativeTime`, whose docstring above argues for the
 * opposite. They serve two different acts. A fleet rail is SCANNED — dozens of
 * rows, one glance, one question ("is this still moving") — and there "2h" beats
 * "2h 14m" because the extra unit is noise in a column that also truncates. An
 * Info tab is READ: one workspace, deliberately opened, and there "5h ago" for
 * something touched at five hours and fifty minutes silently discards the
 * fifty. Rounding down to the largest whole unit is exactly the information a
 * reader came to that surface for.
 *
 * So this is not a `precision` prop on `relativeTime`. A formatter a caller can
 * configure is a formatter two callers configure differently, and the same age
 * would then read two ways with nothing to say which was intended.
 *
 * Two units, largest first, and never a third: "3d 4h 12m" changes no decision
 * that "3d 4h" does not, and it churns the string every minute. The scale runs
 * all the way to years so a long-lived workspace reports its real age instead of
 * "412d ago", which a reader has to divide.
 */
export function preciseAge(iso: string, now: number): string {
  const seconds = Math.round((now - new Date(iso).getTime()) / 1000);
  // Same clamp and same wording as `relativeTime`: a timestamp a second ahead of
  // this browser is clock skew against the daemon's host, never a future event.
  if (!Number.isFinite(seconds) || seconds < MINUTE) return "just now";

  const pair = (whole: number, unit: string, rest: number, sub: number, subUnit: string): string => {
    const remainder = Math.floor((seconds - whole * rest) / sub);
    return remainder > 0 ? `${whole}${unit} ${remainder}${subUnit} ago` : `${whole}${unit} ago`;
  };
  if (seconds < HOUR) return `${Math.floor(seconds / MINUTE)}m ago`;
  if (seconds < DAY) return pair(Math.floor(seconds / HOUR), "h", HOUR, MINUTE, "m");
  if (seconds < MONTH) return pair(Math.floor(seconds / DAY), "d", DAY, HOUR, "h");
  if (seconds < YEAR) return pair(Math.floor(seconds / MONTH), "mo", MONTH, DAY, "d");
  return pair(Math.floor(seconds / YEAR), "y", YEAR, MONTH, "mo");
}

/**
 * How long something has been RUNNING — "3d 4h", "2h 14m", "6m".
 *
 * WHY THIS EXISTS BESIDE `relativeTime`, WHICH TAKES THE SAME ARGUMENTS AND
 * RETURNS A SIMILAR-LOOKING STRING. They answer different questions off one
 * input. `relativeTime` answers "when did this happen" and its "ago" phrasing
 * places the instant in the past; uptime answers "how long has this been up",
 * which is a DURATION that is still accruing. "Started 2h ago" and "up for 2h"
 * are the same arithmetic and different claims, and a service that has been up
 * two hours is not an event that happened two hours ago — the first is a state,
 * the second is history. Do not fold these back into one function.
 *
 * Two units, largest first, because the third never changes a decision: nobody
 * reads "3d 4h 12m" and acts differently than they would on "3d 4h", and the
 * extra unit churns the string every minute for nothing.
 *
 * IT RETURNS ONLY EVER A MEASUREMENT, INCLUDING THE SUB-MINUTE CASE. Callers
 * wrap this in prose — the rail says "up for …" — so a friendlier "just started"
 * came out as "up for just started". The general rule, worth more than the one
 * bug: a formatter whose caller composes it into a sentence may only return
 * something that fits the sentence, which means a value and never a phrase.
 */
export function durationSince(iso: string, now: number): string {
  const elapsed = Math.round((now - new Date(iso).getTime()) / 1000);
  if (!Number.isFinite(elapsed)) return "unknown";
  // A negative elapsed is clock skew between this browser and the daemon's
  // host, not a service that starts in the future.
  const seconds = Math.max(0, elapsed);
  // "<1m" and not "just started": this returns a DURATION that a caller
  // phrases, and the caller says "up for …" — which made the friendlier wording
  // read as "up for just started". A formatter that only ever returns a
  // measurement cannot be composed into a sentence that does not parse.
  if (seconds < MINUTE) return "<1m";

  const days = Math.floor(seconds / DAY);
  const hours = Math.floor((seconds % DAY) / HOUR);
  const minutes = Math.floor((seconds % HOUR) / MINUTE);
  if (days > 0) return hours > 0 ? `${days}d ${hours}h` : `${days}d`;
  if (hours > 0) return minutes > 0 ? `${hours}h ${minutes}m` : `${hours}h`;
  return `${minutes}m`;
}

/**
 * How long UNTIL a future instant — "4d 19h", "2h 14m", "<1m".
 *
 * The mirror of `durationSince`, and it exists because `relativeTime` refuses
 * this case by design: it clamps a future instant to "just now" on the stated
 * grounds that "Grove has no future timestamps to show". That stopped being
 * true — a quota window's `resets_at` is a scheduled instant in the future, and
 * a reader compares it against *now*, not against a calendar. "resets in 4d 19h"
 * is read at a glance where "resets Aug 15, 2026, 4:00 AM" is arithmetic.
 *
 * Same two-unit rule and same measurement-only contract as `durationSince`: the
 * caller writes "resets in …", so this may return a value and never a phrase.
 */
export function durationUntil(iso: string, now: number): string {
  const remaining = Math.round((new Date(iso).getTime() - now) / 1000);
  if (!Number.isFinite(remaining)) return "unknown";
  // Already elapsed: a reset instant the clock has passed is a reading Grove
  // has not refreshed yet, not a negative duration.
  const seconds = Math.max(0, remaining);
  if (seconds < MINUTE) return "<1m";

  const days = Math.floor(seconds / DAY);
  const hours = Math.floor((seconds % DAY) / HOUR);
  const minutes = Math.floor((seconds % HOUR) / MINUTE);
  if (days > 0) return hours > 0 ? `${days}d ${hours}h` : `${days}d`;
  if (hours > 0) return minutes > 0 ? `${hours}h ${minutes}m` : `${hours}h`;
  return `${minutes}m`;
}

/**
 * The same span, spelled out to TWO units and marked approximate —
 * "~1 day 23 hours", "~2 hours 14 minutes".
 *
 * WHY THIS IS NOT `durationUntil` WITH A FLAG. They make different claims, and
 * the `~` is the difference. `durationUntil` reports a SCHEDULE the provider
 * published: a window really does reset at that instant. This reports an
 * EXTRAPOLATION — where a burn rate lands if nothing changes — and the input is
 * a percentage both providers round to WHOLE numbers, so the arithmetic behind
 * it genuinely does not support exactness.
 *
 * IT ROUNDED TO ONE UNIT FOR EXACTLY THAT REASON, AND THAT WAS THE WRONG TRADE.
 * "~1 day" covers everything from 24 to 47 hours, so a reader could not tell
 * "tomorrow morning" from "the day after" — the imprecision the rounding was
 * protecting against was smaller than the imprecision it introduced. Two units
 * plus the `~` is the honest combination: the granularity is real, and the
 * marker still says the whole figure is an estimate. Precision and accuracy are
 * different claims, and only the second one was ever in doubt here.
 *
 * Spelled-out units, because this is read inside a sentence: "2d" is a token,
 * "2 days" is prose. Measurement only, never a phrase — the caller supplies
 * "…in".
 */
export function approxDurationUntil(iso: string, now: number): string {
  const remaining = Math.round((new Date(iso).getTime() - now) / 1000);
  if (!Number.isFinite(remaining)) return "unknown";
  const seconds = Math.max(0, remaining);
  if (seconds < MINUTE) return "<1 minute";

  const unit = (count: number, name: string): string =>
    `${count} ${name}${count === 1 ? "" : "s"}`;
  // FLOOR, not round, on both halves: rounding the remainder up can carry it to
  // a full unit and print "~1 day 24 hours".
  const pair = (whole: number, name: string, size: number, sub: number, subName: string): string => {
    const rest = Math.floor((seconds - whole * size) / sub);
    return rest > 0 ? `~${unit(whole, name)} ${unit(rest, subName)}` : `~${unit(whole, name)}`;
  };
  if (seconds < HOUR) return `~${unit(Math.floor(seconds / MINUTE), "minute")}`;
  if (seconds < DAY) return pair(Math.floor(seconds / HOUR), "hour", HOUR, MINUTE, "minute");
  return pair(Math.floor(seconds / DAY), "day", DAY, HOUR, "hour");
}

/**
 * The exact instant, spelled out.
 *
 * Exported because a caller that shows a relative age often has a SECOND
 * timestamp to put in the same tooltip — the rail pairs "updated 2h ago" with
 * the creation time — and two spellings of an exact instant in one hover is how
 * a surface starts looking assembled by different people.
 */
export function absoluteTime(iso: string): string {
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? iso : at.toLocaleString();
}

/**
 * `null` until mounted, then the clock, re-read once a minute.
 *
 * Shared by both components below because the hydration rule is the same for
 * each and must not be re-derived: the first CLIENT render has to produce
 * exactly what the server produced, and neither a relative age nor a duration
 * can while the server has a different clock. A minute is the smallest unit
 * either one renders, so a faster tick repaints rows for a string that cannot
 * have changed.
 *
 * EXPORTED so a surface that renders a duration inside its own layout — the
 * usage page's quota meters — reads the same clock on the same tick instead of
 * standing up a second one. A second `useState`+`setInterval` elsewhere would
 * not just duplicate this; it would drift out of phase with it, so two durations
 * on one screen could be computed a minute apart. There is one clock.
 */
export function useNow(): number | null {
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => {
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), MINUTE * 1000);
    return () => clearInterval(id);
  }, []);
  return now;
}

export function RelativeTime({
  iso,
  className,
}: {
  iso: string | null | undefined;
  className?: string;
}): React.ReactNode {
  const now = useNow();

  if (!iso) return <span className={className}>unknown</span>;
  const exact = absoluteTime(iso);
  return (
    <time dateTime={iso} title={exact} className={className} data-testid="relative-time">
      {now === null ? exact : relativeTime(iso, now)}
    </time>
  );
}

/**
 * `RelativeTime`'s detail-surface twin — see `preciseAge` for why both exist.
 *
 * Identical hydration contract: the absolute instant is what the server paints
 * and what the first client render must reproduce, so a narrow column has to cap
 * and truncate it. The Info tab's field rows are wide enough not to.
 */
export function PreciseAge({
  iso,
  className,
}: {
  iso: string | null | undefined;
  className?: string;
}): React.ReactNode {
  const now = useNow();

  if (!iso) return <span className={className}>unknown</span>;
  const exact = absoluteTime(iso);
  return (
    <time dateTime={iso} title={exact} className={className} data-testid="precise-age">
      {now === null ? exact : preciseAge(iso, now)}
    </time>
  );
}

/**
 * "up for 3d 4h" — a service's uptime, with the start instant on hover.
 *
 * The icon is owned HERE rather than passed in, for the same reason the entity
 * labels own theirs: a duration with no mark is just a number, and "2h" beside
 * a version string does not say what it is measuring. It is sized in `em` so it
 * tracks whatever type size the surface sets, matching the entity vocabulary.
 *
 * `Timer` and not `Clock`: a clock face reads as a point in time, which is the
 * exact confusion this component exists to remove.
 */
export function Uptime({
  iso,
  className,
}: {
  iso: string | null | undefined;
  className?: string;
}): React.ReactNode {
  const now = useNow();

  if (!iso) return <span className={className}>unknown</span>;
  const exact = absoluteTime(iso);
  return (
    <time
      dateTime={iso}
      title={`Running since ${exact}`}
      className={className}
      data-testid="uptime"
    >
      <TimerIcon aria-hidden className="mr-1 inline size-[1em] shrink-0 align-[-0.125em]" />
      {now === null ? exact : `up for ${durationSince(iso, now)}`}
    </time>
  );
}
