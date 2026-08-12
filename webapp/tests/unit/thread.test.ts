import { describe, expect, it } from "vitest";

import { THREAD_INSET, THREAD_WIDTH } from "@/components/grove/workspace/thread-width";
import {
  USER_MESSAGE_CLAMP_HEIGHT,
  USER_MESSAGE_CLAMP_LINES,
  clampStyle,
  showsToggle,
} from "@/components/grove/workspace/user-message";

describe("THREAD_WIDTH", () => {
  it("keeps upstream's measure for a half-width split column", () => {
    expect(THREAD_WIDTH.split).toBe("44rem");
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
  it("scales on the THREAD's container, never on the viewport", () => {
    // A viewport breakpoint would hand the widest margin to a split pane, which
    // is the narrowest column on the page. `@` variants resolve against the
    // thread root's own `@container`, so the pane's width is what decides.
    for (const step of THREAD_INSET.split(" ").filter((c) => c.includes(":"))) {
      expect(step).toMatch(/^@/);
    }
  });

  it("leaves a split pane on upstream's tight inset", () => {
    // The margin must not double up where horizontal space is already halved.
    expect(THREAD_INSET.split(" ")[0]).toBe("px-4");
  });

  it("widens monotonically, so a bigger pane never gets a smaller margin", () => {
    const sizes = THREAD_INSET.split(" ").map((c) => Number(c.split("px-")[1]));
    expect(sizes).toEqual([...sizes].sort((a, b) => a - b));
    expect(sizes.length).toBeGreaterThan(1);
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
