import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ChangesTab } from "@/components/grove/workspace/changes-tab";
import type { CommitSummaryView, WorkspacePeekView } from "@/lib/grove/api";

/**
 * "No commits on this branch yet" must be a MEASUREMENT, never a default.
 *
 * The bug this pins: the card read `commitEvents(commits ?? [])` and branched
 * on the resulting length, so `undefined` — the value `useWorkspaceCommits`
 * holds for the whole of its first fetch, and again on every remount of the
 * Work panel — rendered the empty-state copy. A branch with fifty commits
 * announced it had none, confidently, until the request landed. That is a wrong
 * answer rather than a missing one, which is the class of defect this repo
 * treats as worse than a spinner.
 *
 * Asserted as THREE distinct renders rather than by describing any one of them:
 * `undefined` (in flight), `[]` (genuinely empty) and a real log must not be
 * confusable, and a future refactor that folds the first two back together
 * fails here.
 */

const PEEK = {
  state: { id: "w1", title: "Some work", branch: "feature/x", base_branch: "main" },
  base_ahead: 2,
  base_behind: 0,
  dirty_files: 1,
  diff_added: 10,
  diff_removed: 3,
} as unknown as WorkspacePeekView;

const COMMITS = [
  { sha: "aaaaaaa", subject: "Add the endpoint", author: "Someone", committed_at: null },
] as unknown as CommitSummaryView[];

const inFlight = renderToStaticMarkup(<ChangesTab peek={PEEK} commits={undefined} />);
const empty = renderToStaticMarkup(<ChangesTab peek={PEEK} commits={[]} />);
const loaded = renderToStaticMarkup(<ChangesTab peek={PEEK} commits={COMMITS} />);

describe("ChangesTab commit states", () => {
  it("does NOT claim an empty branch while the log is still in flight", () => {
    expect(inFlight).not.toContain("No commits on this branch yet.");
  });

  it("shows a loading placeholder instead", () => {
    expect(inFlight).toContain("commits-loading");
  });

  it("still says so when the branch is GENUINELY empty", () => {
    // The other half of the fix: suppressing the false claim must not suppress
    // the true one. An empty array is a real answer and reads as one.
    expect(empty).toContain("No commits on this branch yet.");
    expect(empty).not.toContain("commits-loading");
  });

  it("renders the real log once it lands, with neither other state", () => {
    expect(loaded).toContain("commit-timeline");
    expect(loaded).not.toContain("No commits on this branch yet.");
    expect(loaded).not.toContain("commits-loading");
  });

  it("counts commits in the card description only once they are known", () => {
    // "1 since the fork point" is a claim about data; while loading, the card
    // says only what it can stand behind.
    expect(loaded).toContain("1 since the fork point");
    expect(inFlight).not.toContain("0 since the fork point");
  });
});
