import { describe, expect, it } from "vitest";

import {
  panesShown,
  restoredView,
  restoredWorkTab,
} from "@/components/grove/workspace/selectors";

/**
 * `panesShown` is the seam that drives lazy, once-only pane mounting in
 * `Workspace` (see `components/grove/workspace/index.tsx`): a pane's mounted
 * flag latches true the first render its key appears here, and the flag
 * never latches back false. So the property that matters is not just "what
 * does this return today" but "does a pane, once shown, ever drop out of a
 * later split view" — `split` has to be the union of both single-pane
 * results, not an independent third answer, or a pane that was already
 * mounted for one reason could read as unmounted once `split` is reached by
 * a different path.
 */
describe("panesShown", () => {
  it("shows only the transcript on the transcript pane", () => {
    expect(panesShown("transcript")).toEqual(["transcript"]);
  });

  it("shows only the work panel on the work pane", () => {
    expect(panesShown("work")).toEqual(["work"]);
  });

  it("shows both panes on split", () => {
    expect(panesShown("split")).toEqual(
      expect.arrayContaining(["transcript", "work"]),
    );
    expect(panesShown("split")).toHaveLength(2);
  });

  it("split is the union of what either single pane shows alone", () => {
    const single = new Set([...panesShown("transcript"), ...panesShown("work")]);
    expect(new Set(panesShown("split"))).toEqual(single);
  });
});

/**
 * `restoredView` and `restoredWorkTab` are the pure half of restoring a
 * workspace's pane choice from `localStorage` (the I/O itself — the actual
 * `getItem` call, the `typeof window` guard — lives at the edge in
 * `Workspace`, never here). What has to be pinned by a test is exactly what
 * crossing that boundary can do to a value: the raw read is `unknown`, not
 * `PaneView`/`PanelTab`, because nothing stops a missing key, a value from a
 * different build, or a hand-edited store from showing up as the stored
 * value.
 */
describe("restoredView", () => {
  it("restores a stored value that is still a real pane", () => {
    expect(restoredView("work", true)).toBe("work");
    expect(restoredView("split", true)).toBe("split");
  });

  it("falls back to transcript for a missing key", () => {
    expect(restoredView(null, true)).toBe("transcript");
  });

  it("falls back to transcript for garbage — an old build's value, a wrong type, a hand-edited store", () => {
    expect(restoredView("minimized", true)).toBe("transcript");
    expect(restoredView(42, true)).toBe("transcript");
    expect(restoredView(undefined, true)).toBe("transcript");
    expect(restoredView({ view: "work" }, true)).toBe("transcript");
  });

  it("still resolves a restored split through visiblePane on a narrow viewport", () => {
    // The exact bug `visiblePane` exists for, reached by a second route: a
    // `split` persisted from a wide session must not leave no tab selected
    // when it is restored on a narrow one.
    expect(restoredView("split", false)).toBe("work");
  });

  it("leaves a restored single pane alone at any width", () => {
    for (const offered of [true, false]) {
      expect(restoredView("transcript", offered)).toBe("transcript");
      expect(restoredView("work", offered)).toBe("work");
    }
  });
});

describe("restoredWorkTab", () => {
  it("restores a stored value that is still a real tab", () => {
    for (const tab of ["terminal", "changes", "files", "info", "controls"]) {
      expect(restoredWorkTab(tab)).toBe(tab);
    }
  });

  it("falls back to info — the same default a fresh Work/Split entry lands on — for a missing key", () => {
    expect(restoredWorkTab(null)).toBe("info");
    expect(restoredWorkTab(undefined)).toBe("info");
  });

  it("falls back to info for garbage", () => {
    expect(restoredWorkTab("history")).toBe("info");
    expect(restoredWorkTab(7)).toBe("info");
    expect(restoredWorkTab({ tab: "changes" })).toBe("info");
  });
});
