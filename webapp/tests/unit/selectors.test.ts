import { describe, expect, it } from "vitest";

import {
  panesShown,
  storedView,
  storedWorkTab,
  resolvedWorkspaceSelection,
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
    const single = new Set([
      ...panesShown("transcript"),
      ...panesShown("work"),
    ]);
    expect(new Set(panesShown("split"))).toEqual(single);
  });
});

/**
 * A saved choice is distinct from no choice. The page deliberately carries
 * `null` through to the transcript rule instead of converting it into an
 * arbitrary pane/tab: that is what lets the first real turn update an automatic
 * default without overwriting a reader's explicit choice.
 */
describe("resolvedWorkspaceSelection", () => {
  it("opens the live terminal until a transcript exists", () => {
    expect(
      resolvedWorkspaceSelection(false, { view: null, workTab: null }),
    ).toEqual({
      view: "work",
      workTab: "terminal",
    });
  });

  it("opens the split once there is a real transcript", () => {
    expect(
      resolvedWorkspaceSelection(true, { view: null, workTab: null }),
    ).toEqual({
      view: "split",
      workTab: "info",
    });
  });

  it("keeps every reader choice when the first turn arrives asynchronously", () => {
    const selected = {
      view: "transcript" as const,
      workTab: "changes" as const,
    };

    expect(resolvedWorkspaceSelection(false, selected)).toEqual(selected);
    expect(resolvedWorkspaceSelection(true, selected)).toEqual(selected);
  });

  it("keeps a stored choice that is only partially specified", () => {
    expect(
      resolvedWorkspaceSelection(true, { view: "work", workTab: null }),
    ).toEqual({ view: "work", workTab: "info" });
  });
});

describe("storedView", () => {
  it("returns a valid saved pane as an explicit choice", () => {
    expect(storedView("work")).toBe("work");
    expect(storedView("split")).toBe("split");
  });

  it("keeps absence and garbage distinct from a default", () => {
    expect(storedView(null)).toBeNull();
    expect(storedView("minimized")).toBeNull();
    expect(storedView(42)).toBeNull();
    expect(storedView(undefined)).toBeNull();
    expect(storedView({ view: "work" })).toBeNull();
  });
});

describe("storedWorkTab", () => {
  it("returns a valid saved tab as an explicit choice", () => {
    for (const tab of ["terminal", "changes", "files", "info", "controls"]) {
      expect(storedWorkTab(tab)).toBe(tab);
    }
  });

  it("keeps absence and garbage distinct from a default", () => {
    expect(storedWorkTab(null)).toBeNull();
    expect(storedWorkTab("history")).toBeNull();
    expect(storedWorkTab(7)).toBeNull();
    expect(storedWorkTab({ tab: "changes" })).toBeNull();
  });
});
