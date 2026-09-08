import { describe, expect, it } from "vitest";

import { divergenceFacts } from "@/components/grove/workspace/selectors";

/**
 * The Changes tab's five git counters answer THREE different questions, and the
 * failure this file guards is that they stop looking like it.
 *
 * `dirty_files` is uncommitted paths in the worktree right now; `diff_added` /
 * `diff_removed` are lines on the branch since its diff base — not the agent's
 * cumulative edits; `base_ahead` / `base_behind` are commits against the base
 * BRANCH, not against a remote upstream. Rendered as five bare numbers they
 * read as one measurement taken five ways, which is how a reader concludes
 * their branch is 8 commits ahead when 8 is the file count.
 *
 * The other half is the withholding. Ahead and behind are the only two figures
 * derived from the base NAME, so with no separate base the range is
 * `HEAD..branch` — structurally empty however much work has been done. A zero
 * that cannot be anything else is not a measurement, and printing it would tell
 * a reader their branch has diverged by nothing.
 */

const PEEK = {
  base_ahead: 3,
  base_behind: 1,
  diff_added: 11_200,
  diff_removed: 240,
  dirty_files: 8,
};

describe("divergenceFacts", () => {
  it("withholds ahead and behind without a base branch, and never zeroes them", () => {
    const keys = divergenceFacts(PEEK, false).map((fact) => fact.key);

    expect(keys).toEqual(["dirty", "added", "removed"]);
    expect(keys).not.toContain("ahead");
    expect(keys).not.toContain("behind");
    // The three that remain are anchored on `base_commit` and stay true, so a
    // no-base workspace loses two facts rather than gaining two false ones.
    expect(divergenceFacts(PEEK, false)).toHaveLength(3);
  });

  it("reports all five against a base branch, base divergence first", () => {
    expect(divergenceFacts(PEEK, true).map((fact) => fact.key)).toEqual([
      "ahead",
      "behind",
      "dirty",
      "added",
      "removed",
    ]);
  });

  it("names the unit in every label, because the three scopes are not one scope", () => {
    const labels = Object.fromEntries(
      divergenceFacts(PEEK, true).map((fact) => [fact.key, fact.label]),
    );

    expect(labels.ahead).toBe("Commits ahead");
    expect(labels.behind).toBe("Commits behind");
    expect(labels.dirty).toBe("Dirty files");
    expect(labels.added).toBe("Lines added");
    expect(labels.removed).toBe("Lines removed");
  });

  it("puts the sign on the figure so the tone is never the only carrier", () => {
    const facts = Object.fromEntries(
      divergenceFacts(PEEK, true).map((fact) => [fact.key, fact]),
    );

    expect(facts.added.value).toBe("+11.2K");
    expect(facts.removed.value).toBe("−240");
    expect(facts.added.tone).toBe("added");
    expect(facts.removed.tone).toBe("removed");
    // Abbreviated above four digits, with the exact figure one hover away (§3).
    expect(facts.added.title).toContain("11,200");
    expect(facts.dirty.title).toContain("uncommitted files in the worktree");
  });

  it("keeps a real zero quiet rather than colouring a change that did not happen", () => {
    const quiet = divergenceFacts({ ...PEEK, diff_added: 0, dirty_files: 0 }, true);
    const byKey = Object.fromEntries(quiet.map((fact) => [fact.key, fact]));

    expect(byKey.added.value).toBe("+0");
    expect(byKey.added.tone).toBeUndefined();
    expect(byKey.dirty.tone).toBeUndefined();
    // A measured zero is still a figure — it is rendered, never withheld.
    expect(byKey.added.value).not.toBeNull();
    expect(byKey.dirty.value).toBe("0");
  });

  it("claims no provenance it does not have — git said these, Grove did not compute them", () => {
    expect(divergenceFacts(PEEK, true).every((fact) => !fact.derived)).toBe(true);
  });
});
