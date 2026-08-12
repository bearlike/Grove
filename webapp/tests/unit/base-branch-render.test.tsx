import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ChangesTab } from "@/components/grove/workspace/changes-tab";
import { InfoTab } from "@/components/grove/workspace/info-tab";
import { WorkspaceCard } from "@/components/grove/fleet/workspace-card";
import { workspace } from "@/tests/fixtures/fleet";
import { FIXTURE_PEEK } from "../e2e/_fixtures";

/**
 * The three surfaces that print a base branch, rendered against a workspace that
 * genuinely has none.
 *
 * `baseBranchOf` is unit-tested next door; this is the seam test, and it is the
 * one that would have caught the shipped defect. A helper can be perfect and
 * still reach nothing — the bug was a real ref name arriving at a renderer that
 * trusted it, so what has to be pinned is that no surface prints `HEAD` as a
 * branch, and that each writes its OWN sentence for the absence rather than
 * falling back to a shared one.
 *
 * `FIXTURE_PEEK` is a capture of a real root workspace, so it carries
 * `branch: "main"` with `base_branch: "HEAD"` without anyone having to
 * construct the case by hand.
 */

/** How many `BranchLabel`s a markup string contains. Counted on the component's
 * own testid, because `Branch:` appears twice per label — once in `title`, once
 * in the `sr-only` type word. */
const branchLabels = (html: string): number =>
  (html.match(/data-testid="entity-branch"/g) ?? []).length;

const rootCard = workspace({ id: "root", branch: "main" });
// The fleet fixture builds a worktree workspace; make it the root case the peek
// already is, so all three assertions are about the same kind of workspace.
const asRoot = {
  ...rootCard,
  state: { ...rootCard.state, base_branch: "HEAD" },
};

describe("a workspace with no separate base branch", () => {
  it("fleet card drops the base chip AND the arrow it hangs off", () => {
    const html = renderToStaticMarkup(<WorkspaceCard workspace={asRoot} repoName="Grove" />);

    // One branch glyph, not two, and no dangling relation mark.
    expect(html).not.toContain("←");
    expect(branchLabels(html)).toBe(1);
    expect(html).toContain("main");
  });

  it("Info says there is no base rather than printing one called HEAD", () => {
    const html = renderToStaticMarkup(
      <QueryClientProvider client={new QueryClient()}>
        <InfoTab peek={FIXTURE_PEEK} activity={null} onKilled={() => {}} />
      </QueryClientProvider>,
    );

    expect(html).toContain("no separate base branch");
    expect(html).not.toContain("from HEAD");
  });

  it("Changes names the anchor it actually measures against", () => {
    const html = renderToStaticMarkup(<ChangesTab peek={FIXTURE_PEEK} commits={[]} />);

    expect(html).toContain("against the commit this workspace started from");
    // ahead/behind are derived from the base NAME, so with no base they can only
    // ever report zero — withheld, not printed as a measurement.
    expect(html).not.toContain(">ahead<");
    expect(html).not.toContain(">behind<");
    // The three anchored on `base_commit` are still true and still shown.
    expect(html).toContain(">dirty<");
  });

  it("still prints a real base branch on a normal worktree workspace", () => {
    // The control. Without it, "renders nothing" would pass every assertion
    // above for entirely the wrong reason.
    const html = renderToStaticMarkup(
      <WorkspaceCard workspace={workspace({ id: "w1", branch: "feat/rail" })} repoName="Grove" />,
    );

    expect(html).toContain("←");
    expect(branchLabels(html)).toBe(2);
    expect(html).toContain("main");
  });
});
