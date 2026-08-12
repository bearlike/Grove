import { describe, expect, it } from "vitest";

import {
  approxDurationUntil,
  durationSince,
  durationUntil,
  preciseAge,
  relativeTime,
} from "@/components/grove/relative-time";

const START = Date.parse("2026-08-10T12:00:00Z");
const at = (seconds: number): number => START + seconds * 1000;
const ISO = "2026-08-10T12:00:00Z";

/**
 * Two formatters over one input, answering two different questions. The tests
 * are written to fail if anyone folds them back together, because the strings
 * are similar enough that it looks like a duplication worth removing.
 */
describe("durationSince — how long it has been UP", () => {
  it("reads as a duration, never as a point in the past", () => {
    // The bug this replaced: uptime rendered "2h ago", which says an event
    // happened and stopped. A running service is a state that is still accruing.
    expect(durationSince(ISO, at(7200))).toBe("2h");
    expect(durationSince(ISO, at(7200))).not.toContain("ago");
  });

  it("carries two units, largest first", () => {
    expect(durationSince(ISO, at(3 * 86400 + 4 * 3600))).toBe("3d 4h");
    expect(durationSince(ISO, at(2 * 3600 + 14 * 60))).toBe("2h 14m");
    expect(durationSince(ISO, at(6 * 60))).toBe("6m");
  });

  it("drops a zero unit rather than printing it", () => {
    expect(durationSince(ISO, at(3 * 86400))).toBe("3d");
    expect(durationSince(ISO, at(5 * 3600))).toBe("5h");
  });

  it("stops at two units — the third never changes a decision", () => {
    expect(durationSince(ISO, at(3 * 86400 + 4 * 3600 + 12 * 60))).toBe("3d 4h");
  });

  it("returns a MEASUREMENT under a minute, so it composes into the caller's sentence", () => {
    // It once returned "just started", which the rail rendered as
    // "up for just started". A formatter a caller wraps in prose must only ever
    // return something that fits the prose.
    expect(durationSince(ISO, at(0))).toBe("<1m");
    expect(durationSince(ISO, at(59))).toBe("<1m");
    expect(`up for ${durationSince(ISO, at(30))}`).toBe("up for <1m");
  });

  it("treats a future start as clock skew, not as a negative uptime", () => {
    // The daemon's host and this browser are different clocks.
    expect(durationSince(ISO, at(-30))).toBe("<1m");
    expect(durationSince(ISO, at(-99999))).toBe("<1m");
  });

  it("does not invent a duration from an unparseable instant", () => {
    expect(durationSince("not-a-date", at(0))).toBe("unknown");
  });
});

describe("relativeTime and durationSince stay distinct", () => {
  it("phrase the SAME elapsed time differently, because they claim different things", () => {
    const now = at(2 * 3600);
    expect(relativeTime(ISO, now)).toBe("2h ago");
    expect(durationSince(ISO, now)).toBe("2h");
  });
});

/**
 * The forward-looking pair. `relativeTime` deliberately refuses a future instant
 * ("Grove has no future timestamps to show") — a quota window's reset and a
 * burn-rate forecast are both exactly that, so they get their own two.
 */
describe("durationUntil — a SCHEDULE the provider published", () => {
  const FUTURE = "2026-08-14T12:00:00Z";
  const until = (seconds: number): number => Date.parse(FUTURE) - seconds * 1000;

  it("carries two units, mirroring durationSince", () => {
    expect(durationUntil(FUTURE, until(4 * 86400 + 19 * 3600))).toBe("4d 19h");
    expect(durationUntil(FUTURE, until(2 * 3600 + 14 * 60))).toBe("2h 14m");
    expect(durationUntil(FUTURE, until(6 * 60))).toBe("6m");
  });

  it("composes into the caller's sentence and never becomes one", () => {
    expect(`resets in ${durationUntil(FUTURE, until(30))}`).toBe("resets in <1m");
  });

  it("treats an instant the clock has passed as elapsed, not as negative", () => {
    // A reset time in the past is a reading Grove has not refreshed yet.
    expect(durationUntil(FUTURE, until(-9999))).toBe("<1m");
  });

  it("does not invent a duration from an unparseable instant", () => {
    expect(durationUntil("not-a-date", until(0))).toBe("unknown");
  });
});

describe("approxDurationUntil — an EXTRAPOLATION, and it says so", () => {
  const FUTURE = "2026-08-14T12:00:00Z";
  const until = (seconds: number): number => Date.parse(FUTURE) - seconds * 1000;

  it("carries TWO units and still marks the whole figure approximate", () => {
    // It rounded to one unit to avoid dressing a guess as a measurement — the
    // input is a percentage the providers round to whole numbers. That
    // overcorrected: "~1 day" spans 24 to 47 hours, so the reader could not tell
    // tomorrow from the day after. The `~` is what carries the uncertainty;
    // granularity and accuracy are different claims.
    expect(approxDurationUntil(FUTURE, until(2 * 86400 + 4 * 3600))).toBe("~2 days 4 hours");
    expect(approxDurationUntil(FUTURE, until(5 * 3600 + 20 * 60))).toBe("~5 hours 20 minutes");
    expect(approxDurationUntil(FUTURE, until(40 * 60))).toBe("~40 minutes");
  });

  it("drops a zero remainder rather than padding it", () => {
    expect(approxDurationUntil(FUTURE, until(2 * 86400))).toBe("~2 days");
    expect(approxDurationUntil(FUTURE, until(3 * 3600))).toBe("~3 hours");
  });

  it("floors the remainder, so it can never print a full sub-unit", () => {
    // Rounding the remainder up carries it to a whole unit: "~1 day 24 hours".
    expect(approxDurationUntil(FUTURE, until(86400 + 23 * 3600 + 59 * 60))).toBe("~1 day 23 hours");
  });

  it("spells its units out, because it is read inside a sentence", () => {
    expect(approxDurationUntil(FUTURE, until(2 * 86400))).not.toContain("2d");
  });

  it("agrees with its own singular", () => {
    expect(approxDurationUntil(FUTURE, until(86400))).toBe("~1 day");
    expect(approxDurationUntil(FUTURE, until(3600))).toBe("~1 hour");
  });

  it("makes a DIFFERENT claim from durationUntil over the same span", () => {
    // Same granularity now, still not the same claim: durationUntil reports a
    // schedule the provider published, this reports an extrapolation. The `~`
    // and the spelled-out units are the difference.
    const now = until(2 * 86400 + 4 * 3600);
    expect(durationUntil(FUTURE, now)).toBe("2d 4h");
    expect(approxDurationUntil(FUTURE, now)).toBe("~2 days 4 hours");
  });
});


/**
 * `preciseAge` exists because `relativeTime` keeps only the largest whole unit,
 * which is right for a scanned rail and wrong for a surface opened to read ONE
 * workspace. These tests pin the difference, because the two return
 * similar-looking strings and folding them together looks like a cleanup.
 */
describe("preciseAge — the age a detail surface reads", () => {
  const HOUR = 3600;
  const DAY = 24 * HOUR;

  it("keeps the remainder relativeTime discards", () => {
    const now = at(5 * HOUR + 50 * 60);
    expect(relativeTime(ISO, now)).toBe("5h ago");
    expect(preciseAge(ISO, now)).toBe("5h 50m ago");
  });

  it("carries two units at every scale, largest first", () => {
    expect(preciseAge(ISO, at(2 * HOUR + 14 * 60))).toBe("2h 14m ago");
    expect(preciseAge(ISO, at(3 * DAY + 4 * HOUR))).toBe("3d 4h ago");
    expect(preciseAge(ISO, at(65 * DAY))).toBe("2mo 4d ago");
    expect(preciseAge(ISO, at(400 * DAY))).toBe("1y 1mo ago");
  });

  it("drops a zero remainder rather than padding it", () => {
    expect(preciseAge(ISO, at(3 * HOUR))).toBe("3h ago");
    expect(preciseAge(ISO, at(2 * DAY))).toBe("2d ago");
  });

  it("never emits a third unit — it changes no decision and churns every minute", () => {
    expect(preciseAge(ISO, at(3 * DAY + 4 * HOUR + 12 * 60))).toBe("3d 4h ago");
  });

  it("clamps a future instant to the same wording relativeTime uses", () => {
    // Clock skew between this browser and the daemon's host, not an event that
    // has not happened yet.
    expect(preciseAge(ISO, at(-30))).toBe("just now");
    expect(preciseAge(ISO, at(30))).toBe("just now");
  });
});
