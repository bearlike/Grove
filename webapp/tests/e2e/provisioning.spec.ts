import { test, expect } from "@playwright/test";

// A container workspace is persisted the moment `create` starts and its
// container does not exist for 49 s (warm) to 6.5 min (cold build) afterwards.
// That window used to read OFFLINE — grey, still, and advertising `respawn`,
// the one verb that destroys the build in flight. These pin the replacement:
// the session page must SAY it is building, how long it has been, and what the
// provisioner just wrote — and it must not offer respawn.
//
// The status is rewritten on the BFF response rather than added as a fourth
// fixture workspace: every other spec counts cards, and a permanently
// provisioning fixture would perturb all of them.

test.beforeEach(async ({ page }) => {
  await page.route("**/api/grove/workspaces/w-grove-1/peek", async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    body.state = {
      ...body.state,
      status: "provisioning",
      runtime: "container",
      provision_status: "provisioning",
      provision_started_at: new Date(Date.now() - 131_000).toISOString(),
    };
    await route.fulfill({ response: res, json: body });
  });
});

test("a provisioning workspace reads as working, with the build log a click away", async ({
  page,
}) => {
  await page.goto("/w/w-grove-1");

  const panel = page.getByTestId("provision-panel");
  await expect(panel).toBeVisible();
  // The lifecycle axis names itself — colour is never the only signal.
  await expect(panel.getByTestId("status-badge")).toHaveAttribute(
    "data-status",
    "provisioning",
  );
  // "has this been going long enough to worry" — counted from the start stamp.
  await expect(panel.getByTestId("provision-elapsed")).toContainText("2m");
  // "is it moving" — the last line the provisioner wrote, which no timer says.
  await expect(panel.getByTestId("provision-headline")).toContainText(
    "extracting layers",
  );
  // The expectation that keeps a 6-minute build from reading as a hang.
  await expect(panel).toContainText(/several minutes/i);

  // The log is behind a fold — collapsed by default, real output inside.
  await expect(page.getByTestId("provision-log")).toHaveCount(0);
  await panel.getByTestId("provision-log-toggle").click();
  await expect(page.getByTestId("provision-log")).toContainText(
    "[internal] load build context",
  );
});

test("respawn is not offered while provisioning, but kill still is", async ({ page }) => {
  await page.goto("/w/w-grove-1");
  await page.getByTestId("identity-trigger").click();
  const summary = page.getByTestId("branch-summary");
  await expect(summary).toBeVisible();
  // Respawn would destroy the build in flight; the engine refuses it anyway,
  // so it must not read as the obvious next click either.
  await expect(summary.getByTestId("action-respawn")).toHaveCount(0);
  await expect(summary.getByTestId("action-pause")).toHaveCount(0);
  // Abandoning a build a user no longer wants stays possible.
  await expect(summary.getByTestId("action-kill")).toBeVisible();
});
