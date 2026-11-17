import { expect, test, type Page } from "@playwright/test";

import { FIXTURE_PEEK } from "./_fixtures";
import type { ArtifactRef, WorkspacePeekView } from "@/lib/grove/api";

const id = FIXTURE_PEEK.state.id;
const worktree = FIXTURE_PEEK.state.worktree_path;

const plan: ArtifactRef = {
  id: "aaaaaaaaaaaa",
  kind: "file",
  target: `${worktree}/docs/plan.md`,
  title: "The plan",
};
const doc: ArtifactRef = {
  id: "bbbbbbbbbbbb",
  kind: "link",
  target: "https://docs.example.test/design/42",
  title: null,
};

function peekWith(refs: readonly ArtifactRef[]): WorkspacePeekView {
  const peek = structuredClone(FIXTURE_PEEK);
  peek.state.artifact_refs = [...refs];
  return peek;
}

async function openInfo(page: Page, refs: readonly ArtifactRef[]): Promise<void> {
  await page.route("**/api/grove/workspaces/*/peek", (route) =>
    route.fulfill({ json: peekWith(refs) }),
  );
  // The favicon is a third-party image; a spec must not reach the network for it.
  await page.route("https://docs.example.test/**", (route) => route.abort());
  await page.goto(`/w/${id}`);
  await expect(page.getByTestId("work-panel")).toBeVisible();
  await page.getByTestId("pane-work").click();
  await page.getByTestId("work-panel-tab-info").click();
  await expect(page.getByTestId("info-tab")).toBeVisible();
}

test("a workspace with nothing attached mounts no Artifacts card on Info", async ({ page }) => {
  await openInfo(page, []);
  await expect(page.getByTestId("tickets-card").or(page.getByTestId("tickets-empty"))).toBeVisible();
  await expect(page.getByTestId("artifacts-card")).toHaveCount(0);
});

test("artifacts list under Tickets, a link opens out, and a file renders in the viewer", async ({
  page,
}) => {
  await page.route("**/api/grove/workspaces/*/artifacts/*/content", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/octet-stream",
      body: "# Rollout plan\n\nShip the **artifact** card.\n",
    }),
  );
  await openInfo(page, [plan, doc]);

  const card = page.getByTestId("artifacts-card");
  await expect(card.getByTestId("artifact-count")).toHaveText("2 attached");
  const rows = card.getByTestId("artifact-row");
  await expect(rows).toHaveCount(2);

  // The file shows the agent's title over a path RELATIVE to the checkout,
  // never the host's absolute one, with a file-type icon.
  await expect(rows.nth(0)).toContainText("The plan");
  await expect(rows.nth(0)).toContainText("docs/plan.md");
  await expect(rows.nth(0)).not.toContainText(worktree);
  await expect(rows.nth(0).getByTestId("file-type-icon")).toHaveCount(1);

  // The link is a real anchor to a new tab, and a dead favicon falls back to a mark.
  await expect(rows.nth(1)).toHaveAttribute("href", doc.target);
  await expect(rows.nth(1)).toHaveAttribute("target", "_blank");
  await expect(rows.nth(1).getByTestId("artifact-link-icon")).toBeVisible();

  // It sits directly after the tickets surface in the tab's reading order.
  const order = await page.getByTestId("info-tab").evaluate((tab) =>
    [...tab.children].map((child) => (child as HTMLElement).dataset.testid ?? ""),
  );
  const tickets = order.findIndex((name) => name.startsWith("tickets-"));
  expect(order[tickets + 1]).toBe("artifacts-card");

  await rows.nth(0).click();
  const viewer = page.getByTestId("artifact-viewer");
  await expect(viewer).toBeVisible();
  // Rendered Markdown, not the source: the heading text without its `#`.
  await expect(viewer.getByRole("heading", { name: "Rollout plan" })).toBeVisible({ timeout: 60_000 });
  await expect(viewer.getByText("# Rollout plan")).toHaveCount(0);
});
