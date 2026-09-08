import { describe, expect, it } from "vitest";

import { tabLabelMode } from "@/components/grove/workspace/adaptive-tabs";

describe("tab labels yield before navigation targets", () => {
  const tabs = [{ full: 90, icon: 32 }, { full: 70, icon: 32 }, { full: 60, icon: 32 }];

  it("shows every label when the intrinsic row fits, including gaps", () => {
    expect(tabLabelMode(228, tabs, 4)).toBe("all");
    expect(tabLabelMode(227, tabs, 4)).toBe("active");
  });

  it("reserves the longest active label so selection cannot change modes", () => {
    expect(tabLabelMode(162, tabs, 4)).toBe("active");
    expect(tabLabelMode(161, tabs, 4)).toBe("icons");
  });

  it("keeps all icons when even the minimum targets overflow", () => {
    expect(tabLabelMode(50, tabs, 4)).toBe("icons");
  });

  it("uses actual counts and widths for optional and custom panels", () => {
    expect(tabLabelMode(160, [{ full: 70, icon: 44 }, { full: 60, icon: 44 }], 4)).toBe("all");
    expect(tabLabelMode(160, [...tabs, { full: 300, icon: 44 }], 4)).toBe("icons");
    expect(tabLabelMode(0, [], 4)).toBe("all");
  });
});
