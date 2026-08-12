import parseDiff from "parse-diff";

import type { WorkspaceDiffView } from "@/lib/grove/api";

/**
 * What the Files tab can honestly show, decided in one pure place.
 *
 * The tab shows `git diff` or it shows helper text — there is no third answer,
 * and nothing here reconstructs a diff. An earlier version rebuilt one in the
 * browser from the edits an agent narrated in its transcript; that was a
 * frontend doing a backend's job, and it could not see an edit the agent never
 * narrated (a `sed`, a formatter, a hand fix), so it quietly answered a
 * different question than the one the tab asks.
 *
 * The daemon returns the patch RAW and unparsed because the vendored
 * `DiffViewer` already reads the format and reports per-file counts. That is
 * the whole reason this file contains no differ.
 */

/** One file's row. */
export type FileDiffEntry = {
  path: string;
  displayPath: string;
  additions: number;
  deletions: number;
  /** That file's own slice of the patch, which the viewer parses itself. */
  patch: string;
};

export type FilesSource =
  /** Files changed in the working tree. `truncated` means git's output was cut. */
  | { kind: "diff"; entries: FileDiffEntry[]; truncated: boolean }
  /** A clean tree. A statement of fact, not a failure. */
  | { kind: "clean" }
  /** git could not answer at all, and the reader is owed the reason. */
  | { kind: "unavailable"; reason: DiffUnavailableReason };

/** Why git could not answer. `unknown` also covers a request that never landed. */
export type DiffUnavailableReason = NonNullable<WorkspaceDiffView["reason"]> | "unknown";

/**
 * Read the daemon's answer.
 *
 * The three states are deliberately not collapsible into two. `available:
 * false` means git could not answer — no repo, or a paused workspace whose
 * worktree is gone — while an empty patch means it answered "nothing changed".
 * Sharing copy between them would send someone hunting for changes that were
 * never there, or reassure them that a broken workspace is clean.
 */
export function filesSource(diff: WorkspaceDiffView | undefined): FilesSource {
  if (!diff) return { kind: "unavailable", reason: "unknown" };
  if (!diff.available) return { kind: "unavailable", reason: diff.reason ?? "unknown" };

  const entries = fromPatch(diff.patch);
  if (entries.length === 0) return { kind: "clean" };
  return { kind: "diff", entries, truncated: diff.truncated };
}

/**
 * A unified patch, one entry per file.
 *
 * `parse-diff` reports each file's own additions and deletions, so the counts a
 * collapsed row shows come from the patch itself and cannot disagree with the
 * diff it expands into.
 */
export function fromPatch(patch: string): FileDiffEntry[] {
  return splitByFile(patch).flatMap((slice) => {
    const [file] = parseDiff(slice);
    if (!file) return [];
    const path = file.to && file.to !== "/dev/null" ? file.to : (file.from ?? "");
    if (!path) return [];
    return [
      {
        path,
        displayPath: path,
        additions: file.additions,
        deletions: file.deletions,
        patch: slice,
      },
    ];
  });
}

/** The column totals across a set of entries. */
export function totalsOf(entries: readonly FileDiffEntry[]): {
  additions: number;
  deletions: number;
} {
  let additions = 0;
  let deletions = 0;
  for (const entry of entries) {
    additions += entry.additions;
    deletions += entry.deletions;
  }
  return { additions, deletions };
}

/**
 * Cut a multi-file patch at its `diff --git` headers so each file can be
 * rendered — and collapsed — on its own.
 *
 * This splits an artifact git already produced; it computes nothing. A payload
 * with no such header (a bare `diff -u`) is one file's worth and is passed
 * through whole rather than dropped.
 */
function splitByFile(patch: string): string[] {
  const cuts = [...patch.matchAll(/^diff --git .*$/gm)].map((match) => match.index);
  if (cuts.length === 0) return patch.trim() ? [patch] : [];
  return cuts.map((start, i) => patch.slice(start, cuts[i + 1]));
}
