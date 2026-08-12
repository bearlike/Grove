import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ChangesTab } from "@/components/grove/workspace/changes-tab";
import type { CommitSummaryView, WorkspacePeekView } from "@/lib/grove/api";

/**
 * The THIRD commit state: time-bounded rather than fork-anchored.
 *
 * `changes-tab-loading.test.tsx` pins the first two — `undefined` (in flight)
 * must not read as `[]` (measured empty). This pins the one underneath both:
 * when the daemon has no recorded fork point it answers a *different question*
 * — everything on the branch since the workspace was created — and that answer
 * errs HIGH, because a commit somebody else pushed inside the window is in it.
 * On a root workspace, whose branch is the user's own live branch, that is an
 * ordinary occurrence rather than a corner case.
 *
 * The defect: with no anchor the range was `HEAD..<branch>`, structurally empty
 * however much work was done, so the card printed "No commits on this branch
 * yet" over a branch holding 106 of them. Suppressing that claim is only half
 * the fix — presenting the replacement with the precision of a fork-point
 * answer would trade a false zero for a false attribution.
 */

const peek = (baseCommit: string | null): WorkspacePeekView =>
  ({
    state: {
      id: "w1",
      title: "Some work",
      branch: "main",
      base_branch: "HEAD",
      base_commit: baseCommit,
    },
    base_ahead: 0,
    base_behind: 0,
    dirty_files: 1,
    diff_added: 10,
    diff_removed: 3,
  }) as unknown as WorkspacePeekView;

const commit = (scope: string | null): CommitSummaryView =>
  ({
    sha: "aaaaaaa",
    subject: "Add the endpoint",
    committed_at: "2026-08-11T03:34:01Z",
    scope,
  }) as unknown as CommitSummaryView;

// An anchored workspace: a real fork point, and rows that say so.
const ANCHORED = renderToStaticMarkup(
  <ChangesTab peek={peek("f".repeat(40))} commits={[commit("since_fork_point")]} />,
);
// A pre-anchor record: no fork point, so the daemon answered by time.
const WINDOWED = renderToStaticMarkup(
  <ChangesTab peek={peek(null)} commits={[commit("since_created_at")]} />,
);
// The same record with nothing in the window — the state that used to lie.
const WINDOWED_EMPTY = renderToStaticMarkup(<ChangesTab peek={peek(null)} commits={[]} />);
const WINDOWED_LOADING = renderToStaticMarkup(
  <ChangesTab peek={peek(null)} commits={undefined} />,
);

describe("ChangesTab, time-bounded commit answers", () => {
  it("keeps saying 'since the fork point' when there IS one", () => {
    // The confident answer must not acquire a hedge it has not earned; the
    // whole value of naming the degraded case is that the other case is clean.
    expect(ANCHORED).toContain("1 since the fork point");
    expect(ANCHORED).not.toContain("since this workspace was created");
    expect(ANCHORED).not.toContain("No fork point was recorded");
  });

  it("says which question was answered when there is no fork point", () => {
    expect(WINDOWED).toContain("1 since this workspace was created");
    expect(WINDOWED).not.toContain("since the fork point");
  });

  it("warns that the list may include commits the workspace did not make", () => {
    // The count errs high, so the card states it rather than implying a
    // precision it does not have.
    expect(WINDOWED).toContain("No fork point was recorded");
    expect(WINDOWED).toContain("may include commits the workspace did not make");
  });

  it("never claims an empty BRANCH when it only measured a WINDOW", () => {
    // The original defect, one state further on than the loading bug: an empty
    // answer here means "nothing since this workspace was created", which is a
    // claim about a window. "No commits on this branch yet" is a claim about
    // all of history, and the read never looked there.
    expect(WINDOWED_EMPTY).not.toContain("No commits on this branch yet.");
    expect(WINDOWED_EMPTY).toContain("No commits on this branch since this workspace was created.");
  });

  it("still shows the loading placeholder rather than either empty claim", () => {
    // The loading fix must survive the new branch: `undefined` is not `[]` and
    // is not a windowed empty either.
    expect(WINDOWED_LOADING).toContain("commits-loading");
    expect(WINDOWED_LOADING).not.toContain("No commits on this branch");
  });

  it("reads the scope off the ROWS, not off the workspace, when rows exist", () => {
    // The daemon is the only thing that knows which range it ran. A client that
    // re-derived the rule from `base_commit` would silently go stale the day
    // the daemon's fallback condition changed, and would still report the old
    // answer with full confidence. Rows present => rows decide: here the
    // anchor is absent but the daemon says it answered by fork point anyway.
    const disagreeing = renderToStaticMarkup(
      <ChangesTab peek={peek(null)} commits={[commit("since_fork_point")]} />,
    );
    expect(disagreeing).toContain("1 since the fork point");
    expect(disagreeing).not.toContain("No fork point was recorded");
  });

  it("treats an ABSENT scope as the historical answer, never as degraded", () => {
    // `scope` is nullable: a list that asked no anchoring question makes no
    // claim, and a payload from a daemon that predates the field has none. Both
    // must read as the confident case, or every older client flips to a hedge.
    const legacy = renderToStaticMarkup(
      <ChangesTab peek={peek("f".repeat(40))} commits={[commit(null)]} />,
    );
    expect(legacy).toContain("1 since the fork point");
    expect(legacy).not.toContain("No fork point was recorded");
  });
});
