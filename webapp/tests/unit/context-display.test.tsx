import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ContextMeter } from "@/components/grove/workspace/context-meter";
import { formatContextWindow } from "@/lib/grove/adapters/context";

describe("formatContextWindow", () => {
  it("formats raw counts as compact SI values and rounds the raw fraction half up", () => {
    expect(formatContextWindow(128_450, 200_000)).toMatchObject({
      used: "128.45K",
      size: "200K",
      counts: "128.45K / 200K",
      exactCounts: "128,450 / 200,000",
      percent: "64.23",
      percentValue: 64.23,
    });
  });

  it("carries a WHOLE-percent spelling for the status band, from the same value", () => {
    // Two spellings of one derivation, never two formatters: the Activity card
    // reads `percent` (§3 grants context occupancy its decimals on a surface
    // opened to read one thing) and the permanently visible band reads
    // `percentWhole`. Both round from `percentValue`, which is also what every
    // tone threshold compares, so no surface can print a figure the ramp
    // disagrees with.
    expect(formatContextWindow(128_450, 200_000)?.percentWhole).toBe("64");
    // Rounds, never truncates — 99.6% of a window is not 99%.
    expect(formatContextWindow(199_200, 200_000)?.percentWhole).toBe("100");
    // And it does not clamp, for the same reason `percent` does not.
    expect(formatContextWindow(250_000, 200_000)?.percentWhole).toBe("125");
  });

  it("promotes a rounded compact unit rather than rendering 1,000K", () => {
    expect(formatContextWindow(999_999, 1_000_000)?.used).toBe("1M");
  });

  it("keeps unreported and unusable measurements unknown rather than fabricating zero", () => {
    expect(formatContextWindow(null, 200_000)).toBeNull();
    expect(formatContextWindow(128_450, null)).toBeNull();
    expect(formatContextWindow(128_450, 0)).toBeNull();
    expect(formatContextWindow(128.45, 200_000)).toBeNull();
    expect(formatContextWindow(Number.MAX_SAFE_INTEGER + 1, 200_000)).toBeNull();
  });

  it("does not hide reported overflow by clamping it to 100 percent", () => {
    expect(formatContextWindow(250_000, 200_000)).toMatchObject({
      counts: "250K / 200K",
      percent: "125",
      percentValue: 125,
    });
  });
});

describe("ContextMeter", () => {
  it("keeps both the compact and exact measurements visible, with accessible raw-count percent", () => {
    const html = renderToStaticMarkup(
      <ContextMeter context={{ size: 200_000, used: 128_450, used_fraction: 0 }} />,
    );

    expect(html).toContain("128.45K / 200K");
    expect(html).toContain("128,450 / 200,000 tokens");
    expect(html).toContain("64.23%");
    expect(html).toContain('aria-valuenow="64.23"');
    expect(html).toContain('aria-valuetext="64.23% used"');
    expect(html).toContain("font-normal tabular-nums");
  });

  it("renders a reported overage without presenting it as a full but ordinary window", () => {
    const html = renderToStaticMarkup(
      <ContextMeter context={{ size: 200_000, used: 250_000, used_fraction: 0 }} />,
    );

    expect(html).toContain("125%");
    expect(html).toContain('aria-valuemax="100"');
    expect(html).toContain('aria-valuenow="100"');
    expect(html).toContain('aria-valuetext="125% used (over capacity)"');
    expect(html).toContain("transform:translateX(-0%)");
  });
});
