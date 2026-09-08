import { describe, expect, it } from "vitest";

import { THREAD_INSET, THREAD_WIDTH } from "@/components/grove/workspace/thread-width";
import {
  USER_MESSAGE_CLAMP_HEIGHT,
  USER_MESSAGE_CLAMP_LINES,
  clampStyle,
  showsToggle,
} from "@/components/grove/workspace/user-message";

describe("THREAD_WIDTH", () => {
  it("lets a split column fill its pane — the pane IS the measure", () => {
    // A reader who dragged the split handle has already chosen the measure;
    // capping it again drew a 704px column between two 400px gutters on a
    // 1531px pane, which is the "narrow centered third" this replaced.
    expect(THREAD_WIDTH.split).toBe("100%");
    expect(THREAD_WIDTH.split).not.toMatch(/rem/);
  });

  it("lets the lone transcript use the monitor, but still bounds it", () => {
    // The complaint was a narrow column stranded in the middle of a wide page;
    // the answer is not an unbounded measure, which is unreadable at 34 inches.
    expect(THREAD_WIDTH.full).toContain("100%");
    expect(THREAD_WIDTH.full).toMatch(/\d+rem/);
    expect(THREAD_WIDTH.full).not.toBe(THREAD_WIDTH.split);
  });
});

describe("THREAD_INSET", () => {
  it("is keyed by the same modes as the width — one decision, two halves", () => {
    expect(Object.keys(THREAD_INSET).sort()).toEqual(Object.keys(THREAD_WIDTH).sort());
  });

  it("scales on the THREAD's container, never on the viewport", () => {
    // A viewport breakpoint would hand the widest margin to a split pane, which
    // is the narrowest column on the page. `@` variants resolve against the
    // thread root's own `@container`, so the pane's width is what decides.
    for (const mode of Object.values(THREAD_INSET)) {
      for (const step of mode.split(" ").filter((c) => c.includes(":"))) {
        expect(step).toMatch(/^@/);
      }
    }
  });

  it("holds a filling split column to ONE small symmetric margin", () => {
    // A column that fills has no gutters left over, so a container step would
    // put 80px of padding either side of the text at ordinary split widths.
    expect(THREAD_INSET.split).toBe("px-4");
  });

  it("widens monotonically, so a bigger pane never gets a smaller margin", () => {
    const sizes = THREAD_INSET.full.split(" ").map((c) => Number(c.split("px-")[1]));
    expect(sizes).toEqual([...sizes].sort((a, b) => a - b));
    expect(sizes.length).toBeGreaterThan(1);
    expect(sizes[0]).toBe(4);
  });
});

describe("clampStyle", () => {
  it("caps an overflowing bubble and fades its tail", () => {
    const style = clampStyle(true, true);
    expect(style?.maxHeight).toBe(USER_MESSAGE_CLAMP_HEIGHT);
    expect(style?.overflow).toBe("hidden");
    expect(style?.maskImage).toContain("linear-gradient");
  });

  it("does NOT fade a bubble that fits — a two-line prompt stays crisp", () => {
    const style = clampStyle(true, false);
    expect(style?.maxHeight).toBe(USER_MESSAGE_CLAMP_HEIGHT);
    expect(style?.maskImage).toBeUndefined();
  });

  it("removes the cap entirely once expanded, so the full text lays out", () => {
    expect(clampStyle(false, true)).toBeUndefined();
  });

  it("derives its height from the line count in one place", () => {
    expect(USER_MESSAGE_CLAMP_HEIGHT).toBe(`${USER_MESSAGE_CLAMP_LINES * 1.5}rem`);
  });
});

describe("showsToggle", () => {
  it("stays hidden when nothing is hidden — no affordance on a short prompt", () => {
    expect(showsToggle(false, false)).toBe(false);
  });

  it("appears once the bubble overflows", () => {
    expect(showsToggle(true, false)).toBe(true);
  });

  it("survives expansion, which is what stops overflow being reported", () => {
    // Expanding removes the clamp, so `scrollHeight === clientHeight` and the
    // measurement flips false. Keying only on it would delete the control the
    // user just pressed, stranding them expanded.
    expect(showsToggle(false, true)).toBe(true);
  });
});
