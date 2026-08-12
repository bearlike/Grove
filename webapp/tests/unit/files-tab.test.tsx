import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { filesSource, fromPatch, totalsOf } from "@/components/grove/workspace/files-source";
import { FilesView } from "@/components/grove/workspace/files-tab";
import type { WorkspaceDiffView } from "@/lib/grove/api";

/**
 * The Files tab shows `git diff` or it shows helper text.
 *
 * The rule with teeth is that NOTHING here computes a diff. The tab used to
 * reconstruct one in the browser from the edits an agent narrated; the daemon
 * now sends the patch raw and the vendored viewer parses it, so `diffLines`
 * must never run. It is counted rather than timed because a call count is the
 * actual contract, where a timing budget would just be a flake on a loaded
 * host.
 */
const spy = vi.hoisted(() => ({ diffLines: 0 }));

vi.mock("diff", async (importOriginal) => {
  const actual = await importOriginal<typeof import("diff")>();
  return {
    ...actual,
    diffLines: (...args: Parameters<typeof actual.diffLines>) => {
      spy.diffLines += 1;
      return actual.diffLines(...args);
    },
  };
});

/**
 * Real `git diff` output: a modified file, a new (untracked) file and a
 * deletion. Untracked files are in scope for this route, so `/dev/null` on
 * either side has to resolve to the right name.
 */
const PATCH = `diff --git a/src/alpha.ts b/src/alpha.ts
index 1111111..2222222 100644
--- a/src/alpha.ts
+++ b/src/alpha.ts
@@ -1,4 +1,5 @@
 export function alpha() {
-  return 1;
+  return 2;
+  // changed by hand, never narrated by an agent
 }

diff --git a/docs/beta.md b/docs/beta.md
new file mode 100644
index 0000000..4444444
--- /dev/null
+++ b/docs/beta.md
@@ -0,0 +1,2 @@
+# Beta
+A new line.

diff --git a/src/gone.ts b/src/gone.ts
deleted file mode 100644
index 5555555..0000000
--- a/src/gone.ts
+++ /dev/null
@@ -1,2 +0,0 @@
-export const gone = true;
-
`;

function view(overrides: Partial<WorkspaceDiffView> = {}): WorkspaceDiffView {
  return { patch: PATCH, files: 3, truncated: false, available: true, ...overrides };
}

beforeEach(() => {
  spy.diffLines = 0;
});

describe("filesSource", () => {
  it("reads a patch into one entry per file", () => {
    const source = filesSource(view());

    expect(source).toMatchObject({ kind: "diff", truncated: false });
    expect(source.kind === "diff" && source.entries).toHaveLength(3);
  });

  it("distinguishes a clean tree from a diff git could not produce", () => {
    // Two states that must never share copy: one means there is nothing to
    // see, the other means we cannot see.
    expect(filesSource(view({ patch: "", files: 0 }))).toEqual({ kind: "clean" });
    expect(filesSource(view({ available: false, reason: "not_a_repo", patch: "" }))).toEqual({
      kind: "unavailable",
      reason: "not_a_repo",
    });
  });

  it("treats a missing response as unavailable rather than clean", () => {
    expect(filesSource(undefined)).toEqual({ kind: "unavailable", reason: "unknown" });
  });

  it("carries truncation through instead of swallowing it", () => {
    expect(filesSource(view({ truncated: true }))).toMatchObject({ truncated: true });
  });

  it("computes no diff of its own", () => {
    filesSource(view());
    filesSource(view({ truncated: true }));

    expect(spy.diffLines).toBe(0);
  });
});

describe("fromPatch", () => {
  it("names a file by its post-image, and a deletion by its pre-image", () => {
    expect(fromPatch(PATCH).map((e) => e.path)).toEqual([
      "src/alpha.ts",
      "docs/beta.md",
      "src/gone.ts",
    ]);
  });

  it("takes each file's counts from the patch itself", () => {
    const [alpha, beta, gone] = fromPatch(PATCH);

    expect(alpha).toMatchObject({ additions: 2, deletions: 1 });
    expect(beta).toMatchObject({ additions: 2, deletions: 0 });
    expect(gone).toMatchObject({ additions: 0, deletions: 2 });
    expect(totalsOf(fromPatch(PATCH))).toEqual({ additions: 4, deletions: 3 });
  });

  it("hands each row that file's own slice, not the whole payload", () => {
    const [alpha] = fromPatch(PATCH);

    expect(alpha!.patch).toContain("a/src/alpha.ts");
    expect(alpha!.patch).not.toContain("docs/beta.md");
  });

  it("passes a headerless unified diff through as one file rather than dropping it", () => {
    const bare = `--- a/solo.txt\n+++ b/solo.txt\n@@ -1 +1 @@\n-old\n+new\n`;

    expect(fromPatch(bare)).toHaveLength(1);
  });

  it("partitions the patch — every byte lands in exactly one slice", () => {
    // The property that makes the split safe to trust: losing a byte would
    // silently drop a hunk from a file that still renders, which no count in
    // the UI would contradict. Verified once against a real 190 KB / 45-file
    // `git diff`, where the slices summed to the payload exactly and the
    // per-file counts matched `git diff --shortstat` (+1913 −991).
    const slices = fromPatch(PATCH).map((entry) => entry.patch);

    expect(slices.join("")).toBe(PATCH);
  });
});

describe("FilesView", () => {
  const diff = (overrides: Partial<WorkspaceDiffView> = {}) => filesSource(view(overrides));

  it("renders no diff at all until a row is opened", () => {
    const html = renderToStaticMarkup(<FilesView source={diff()} />);

    expect(html).not.toContain('data-slot="diff-viewer-line"');
    expect(html).not.toContain('data-slot="diff-viewer"');
  });

  it("starts every row collapsed", () => {
    const html = renderToStaticMarkup(<FilesView source={diff()} />);

    expect(html.match(/data-testid="file-diff-row"/g)).toHaveLength(3);
    expect(html).not.toContain('data-state="open"');
  });

  it("lists every changed file — there is no cap left to hide one behind", () => {
    const many = Array.from(
      { length: 60 },
      (_, i) =>
        `diff --git a/src/file${i}.ts b/src/file${i}.ts\n--- a/src/file${i}.ts\n+++ b/src/file${i}.ts\n@@ -1 +1 @@\n-old\n+new\n`,
    ).join("\n");
    const html = renderToStaticMarkup(<FilesView source={filesSource(view({ patch: many }))} />);

    expect(html).toContain("60 changed files");
    expect(html.match(/data-testid="file-diff-row"/g)).toHaveLength(60);
  });

  it("never computes a diff, on any path", () => {
    renderToStaticMarkup(<FilesView source={diff()} />);
    renderToStaticMarkup(<FilesView source={diff({ truncated: true })} />);
    renderToStaticMarkup(<FilesView source={filesSource(view({ patch: "", files: 0 }))} />);

    expect(spy.diffLines).toBe(0);
  });

  it("totals the whole patch in the header", () => {
    const html = renderToStaticMarkup(<FilesView source={diff()} />);

    expect(html).toMatch(/data-testid="file-edit-totals"[\s\S]*?\+4[\s\S]*?−3/);
  });

  it("says so when the daemon cut the patch, and stays silent when it did not", () => {
    // The repo's no-silent-caps rule: the files shown are complete, the LIST is
    // not, and only the response knows.
    expect(renderToStaticMarkup(<FilesView source={diff({ truncated: true })} />)).toContain(
      'data-testid="diff-truncated"',
    );
    expect(renderToStaticMarkup(<FilesView source={diff()} />)).not.toContain(
      'data-testid="diff-truncated"',
    );
  });

  it("tells a clean tree apart from an unreadable one, in words", () => {
    const clean = renderToStaticMarkup(
      <FilesView source={filesSource(view({ patch: "", files: 0 }))} />,
    );
    expect(clean).toContain('data-source="clean"');
    expect(clean).toContain("No uncommitted changes");

    const broken = renderToStaticMarkup(
      <FilesView source={filesSource(view({ available: false, reason: "worktree_missing" }))} />,
    );
    expect(broken).toContain('data-source="unavailable"');
    expect(broken).toContain('data-reason="worktree_missing"');
    expect(broken).toContain("worktree is gone from disk");
    expect(broken).not.toContain("No uncommitted changes");
  });

  it("explains every reason the wire can send", () => {
    for (const reason of ["worktree_missing", "not_a_repo", "git_failed"] as const) {
      const html = renderToStaticMarkup(
        <FilesView source={filesSource(view({ available: false, reason }))} />,
      );
      expect(html).toContain(`data-reason="${reason}"`);
      // A reason with no copy would render an empty paragraph and look broken.
      expect(html).toMatch(/text-muted-foreground">[A-Za-z]/);
    }
  });
});
