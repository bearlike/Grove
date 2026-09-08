import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import {
  panesShown,
  storedView,
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
  it("opens the work pane until a transcript exists, and still lands on Info", () => {
    expect(
      resolvedWorkspaceSelection(false, { view: null, workTab: null }),
    ).toEqual({
      view: "work",
      workTab: "info",
    });
  });

  it("lands on Info on EVERY visit, whatever the transcript is doing", () => {
    // The landing tab is not a function of the data at all — that is the point.
    // A tab that depended on the first turn arriving would move under a reader
    // mid-visit, which is the behaviour `Workspace` keeps null selections for.
    for (const hasTranscript of [false, true]) {
      expect(
        resolvedWorkspaceSelection(hasTranscript, { view: null, workTab: null }).workTab,
      ).toBe("info");
    }
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

/**
 * The work tab is NOT persisted, and this is the census that says so.
 *
 * A source assertion rather than a behavioural one because the mechanism being
 * pinned is an ABSENCE: there is no storage key to read back, so the only thing
 * that can regress is somebody re-adding one. `view` is asserted alongside it
 * so this cannot pass by the whole file having been renamed out from under it.
 */
describe("work-tab persistence", () => {
  const WORKSPACE = readFileSync(
    new URL("../../components/grove/workspace/index.tsx", import.meta.url),
    "utf8",
  );

  it("persists the PANE and nothing else", () => {
    expect(WORKSPACE).toContain("grove-workspace-view:");
    expect(WORKSPACE).not.toContain("grove-workspace-work-tab");
    expect(WORKSPACE).not.toContain("storedWorkTab");
  });
});
