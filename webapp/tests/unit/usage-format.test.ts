import { describe, expect, it } from "vitest";

import {
  abbreviate,
  exact,
  humanize,
  percent,
  projectLabel,
  tokenTotal,
} from "@/components/grove/usage/format";

describe("abbreviate", () => {
  it("leaves anything under a thousand alone, grouped but unrounded", () => {
    expect(abbreviate(0)).toBe("0");
    expect(abbreviate(513)).toBe("513");
    expect(abbreviate(999)).toBe("999");
  });

  it("switches units at exactly one thousand, always with one decimal", () => {
    // 1000 is the boundary the requirement names, and `1K` would read as a
    // different precision from the `1.2K` beside it in the same column.
    expect(abbreviate(1000)).toBe("1.0K");
    expect(abbreviate(12591)).toBe("12.6K");
    expect(abbreviate(204643)).toBe("204.6K");
    expect(abbreviate(8635)).toBe("8.6K");
  });

  it("climbs the whole ladder", () => {
    expect(abbreviate(1_500_000)).toBe("1.5M");
    expect(abbreviate(320_840_783)).toBe("320.8M");
    expect(abbreviate(2_400_000_000)).toBe("2.4B");
    expect(abbreviate(9_829_595_686)).toBe("9.8B");
    expect(abbreviate(1_200_000_000_000)).toBe("1.2T");
    expect(abbreviate(3_400_000_000_000_000)).toBe("3.4Q");
  });

  it("saturates at the largest unit rather than inventing one", () => {
    // Past quadrillions the mantissa is allowed to grow rather than the ladder.
    expect(abbreviate(5_000_000_000_000_000_000)).toBe("5000.0Q");
  });

  it("promotes when rounding would print a four-digit mantissa", () => {
    // 999_999 / 1000 = 999.999, which toFixed(1) renders as "1000.0K".
    expect(abbreviate(999_999)).toBe("1.0M");
    expect(abbreviate(999_949)).toBe("999.9K");
  });

  it("is symmetric across zero", () => {
    expect(abbreviate(-204_643)).toBe("-204.6K");
    expect(abbreviate(-999)).toBe("-999");
    expect(abbreviate(-1000)).toBe("-1.0K");
  });

  it("reports an absent number as unmeasured, never as zero", () => {
    expect(abbreviate(null)).toBe("not measured");
    expect(abbreviate(undefined)).toBe("not measured");
    expect(abbreviate(Number.NaN)).toBe("not measured");
    expect(abbreviate(Number.POSITIVE_INFINITY)).toBe("not measured");
  });
});

describe("exact", () => {
  it("is the grouped full number the tooltip shows", () => {
    expect(exact(204643)).toBe("204,643");
    expect(exact(513)).toBe("513");
  });

  it("agrees with abbreviate about absence", () => {
    expect(exact(null)).toBe("not measured");
  });
});

describe("tokenTotal", () => {
  it("sums the component classes", () => {
    expect(
      tokenTotal({ fresh_input: 10, cache_read: 20, cache_creation: 5, reasoning: 1, output: 4 }),
    ).toBe(40);
  });

  it("EXCLUDES provider_total from the sum — it is a total, not a class", () => {
    // The live shape: Codex reports cache_read + reasoning + output AND its own
    // provider_total over the same tokens. Adding it double-counts the session.
    expect(
      tokenTotal({ cache_read: 100, reasoning: 5, output: 10, provider_total: 115 }),
    ).toBe(115);
  });

  it("falls back to provider_total when no class was reported", () => {
    expect(tokenTotal({ provider_total: 570643 })).toBe(570643);
  });

  it("is null when nothing at all was measured", () => {
    expect(tokenTotal({})).toBeNull();
    expect(tokenTotal(undefined)).toBeNull();
    expect(
      tokenTotal({ fresh_input: null, cache_read: null, provider_total: null }),
    ).toBeNull();
  });
});

describe("percent", () => {
  it("drops a trailing zero decimal the providers pad", () => {
    expect(percent(34.0)).toBe("34%");
    expect(percent(0)).toBe("0%");
    expect(percent(66.66)).toBe("66.7%");
  });
});

describe("projectLabel", () => {
  it("keeps the last two segments, which is what distinguishes two checkouts", () => {
    expect(projectLabel("/a/b/c/owner/repo")).toBe("owner/repo");
  });

  it("names the absence instead of rendering an empty cell", () => {
    expect(projectLabel(null)).toBe("unknown project");
  });
});

describe("humanize", () => {
  it("turns a wire identifier into a label", () => {
    expect(humanize("weekly_all")).toBe("Weekly all");
    expect(humanize("provider_endpoint")).toBe("Provider endpoint");
  });
});
