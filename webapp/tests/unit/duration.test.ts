import { describe, expect, it } from "vitest";

import { NOT_MEASURED, duration } from "@/components/grove/duration";

describe("duration", () => {
  it("prints seconds only below a minute", () => {
    expect(duration(0)).toBe("0s");
    expect(duration(24_000)).toBe("24s");
    expect(duration(59_499)).toBe("59s");
  });

  it("drops to whole minutes once a minute is reached", () => {
    expect(duration(60_000)).toBe("1m");
    expect(duration(48 * 60_000)).toBe("48m");
    // FLOORS, and "60m" is unreachable by construction: at a full hour the
    // formatter has already stepped up to "1h". 59.5 minutes is 59 whole
    // minutes of work, and rounding it up would overstate a figure this column
    // presents as evidence of cost.
    expect(duration(59 * 60_000 + 30_000)).toBe("59m");
  });

  it("switches to hours and minutes, dropping the minutes when they are zero", () => {
    expect(duration(60 * 60_000)).toBe("1h");
    expect(duration(3 * 60 * 60_000 + 12 * 60_000)).toBe("3h 12m");
  });

  it("is a measured zero, never mistaken for an absence", () => {
    // A session Grove could not time and a session that did no work are
    // different facts — this is the latter.
    expect(duration(0)).not.toBe(NOT_MEASURED);
  });

  it("reports an untimed session as unmeasured, never as zero", () => {
    expect(duration(null)).toBe(NOT_MEASURED);
    expect(duration(undefined)).toBe(NOT_MEASURED);
    expect(duration(Number.NaN)).toBe(NOT_MEASURED);
    expect(duration(-1)).toBe(NOT_MEASURED);
  });
});
