import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { FilesSkeleton, FilesView } from "@/components/grove/workspace/files-tab";
import { filesSource } from "@/components/grove/workspace/files-source";

/**
 * The Files tab's loading state, pinned against the state it stands in for.
 *
 * The rule with teeth: a skeleton is the SHAPE of the content, not a grey
 * rectangle where the content will be. This tab used to answer its first fetch
 * with one `h-40` block against a headed card of divided rows, so the whole tab
 * re-laid out when `git diff` returned — the header band, the totals and every
 * row appeared at once and pushed the panel's scroll position with them.
 *
 * So the assertions compare the two renders rather than describing either: the
 * anatomy that survives is the contract, and a future skeleton that drops the
 * card or the row list fails here instead of shipping.
 */
const PATCH = `diff --git a/src/alpha.ts b/src/alpha.ts
index 1111111..2222222 100644
--- a/src/alpha.ts
+++ b/src/alpha.ts
@@ -1,2 +1,2 @@
-const a = 1;
+const a = 2;
 const b = 3;
`;

const loaded = renderToStaticMarkup(
  <FilesView
    source={filesSource({ available: true, patch: PATCH, files: 1, truncated: false, reason: null })}
  />,
);
const pending = renderToStaticMarkup(<FilesSkeleton />);

describe("the Files skeleton", () => {
  it("occupies the tab, exactly as the loaded state does", () => {
    expect(pending).toContain('data-testid="files-tab"');
    expect(loaded).toContain('data-testid="files-tab"');
  });

  it("is a card with a header band, not a bare block", () => {
    expect(pending).toContain('data-slot="card"');
    expect(pending).toContain('data-slot="card-header"');
    expect(pending).toContain('data-slot="card-action"');
  });

  it("stands in one bar per file row", () => {
    const bars = pending.match(/data-slot="skeleton"/g) ?? [];
    // Three in the header (title, description, totals) plus three per row.
    expect(bars.length).toBeGreaterThan(10);
  });

  // The distinction the tab already draws elsewhere and must keep drawing here:
  // a tree that is still being read is not a tree with nothing in it.
  it("never claims the tree is clean or unavailable", () => {
    expect(pending).not.toContain("No uncommitted changes");
    expect(pending).not.toContain("No diff available here");
    expect(pending).toContain('data-source="loading"');
  });
});
