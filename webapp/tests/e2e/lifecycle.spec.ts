import { test, expect, type Page } from "@playwright/test";

// Workspace lifecycle parity (#56). The create flow rides the full cookie → BFF
// → fake-daemon chain (the create POST is non-destructive to the shared read
// fixtures). The stateful pause/resume/kill transitions are isolated per-test
// via page.route, so they never mutate the one shared fake daemon other specs
// read from.

test.describe("create workspace", () => {
  test("opens from the header and submits an auto branch plan", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("create-workspace-button").click();
    const dialog = page.getByTestId("create-dialog");
    await expect(dialog).toBeVisible();
    // Repo defaults to the first known project; agent defaults to claude.
    await expect(dialog.getByTestId("create-repo")).toHaveValue("/repos/Grove");
    await expect(dialog.getByTestId("create-agent")).toHaveValue("claude");
    await dialog.getByTestId("create-title").fill("new feature");

    const reqPromise = page.waitForRequest(
      (r) => r.url().endsWith("/api/grove/workspaces") && r.method() === "POST",
    );
    await dialog.getByTestId("create-submit").click();
    const body = (await reqPromise).postDataJSON();
    expect(body).toMatchObject({
      agent_name: "claude",
      title: "new feature",
      repo_root: "/repos/Grove",
      branch_plan: { kind: "auto" },
    });
    await expect(dialog).toBeHidden();
  });

  test("builds a new-named branch plan from the branch mode", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("create-workspace-button").click();
    const dialog = page.getByTestId("create-dialog");
    await dialog.getByTestId("create-title").fill("named branch");
    await dialog.getByTestId("create-mode").selectOption("new_named");
    await dialog.getByTestId("create-new-name").fill("feature/x");

    const reqPromise = page.waitForRequest(
      (r) => r.url().endsWith("/api/grove/workspaces") && r.method() === "POST",
    );
    await dialog.getByTestId("create-submit").click();
    const body = (await reqPromise).postDataJSON();
    expect(body.branch_plan).toMatchObject({ kind: "new_named", name: "feature/x" });
  });

  test("root mode auto-checks skip-init and explains itself", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("create-workspace-button").click();
    const dialog = page.getByTestId("create-dialog");
    await dialog.getByTestId("create-mode").selectOption("root");
    await expect(dialog.getByTestId("create-skip-init")).toBeChecked();
    await expect(dialog.getByTestId("create-root-note")).toBeVisible();
  });
});

/**
 * A per-test daemon facade for one workspace, fully isolated from the shared
 * fake daemon: `status` flips on pause/resume, kill 404s subsequent peeks. The
 * other detail-page polls (commits/sessions) are quieted so the page renders.
 */
async function mockWorkspace(
  page: Page,
  {
    status = "active",
    placement = "worktree",
    provenance = "grove",
  }: { status?: string; placement?: string; provenance?: string } = {},
) {
  const ws = {
    id: "w-grove-1",
    title: "feat dashboard",
    repo_root: "/repos/Grove",
    branch: "kk/feat-dashboard",
    base_branch: "main",
    worktree_path: "/repos/Grove/.worktrees/dash",
    tmux_session: "grove-dash",
    agent_name: "claude",
    created_at: "2026-05-09T10:00:00Z",
    updated_at: "2026-05-09T10:30:00Z",
    paused_at: null,
    error_detail: null,
    description: null,
    init_status: "ok",
    init_duration_ms: 350,
    branch_provenance: provenance,
    placement,
  };
  const ref = { status, killed: false };
  const json = (status: number, body: unknown) => ({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });

  await page.route("**/api/grove/workspaces/w-grove-1/peek", (route) => {
    if (ref.killed) {
      return route.fulfill(
        json(404, { detail: { error: "workspace_not_found", message: "gone" } }),
      );
    }
    return route.fulfill(
      json(200, {
        state: { ...ws, status: ref.status },
        base_ahead: 3,
        base_behind: 0,
        diff_added: 124,
        diff_removed: 17,
        dirty_files: 2,
        recent_commits: [],
        agent_snapshot: null,
        snapshot_taken_at: null,
      }),
    );
  });
  await page.route("**/api/grove/workspaces/w-grove-1/pause", (route) => {
    ref.status = "paused";
    return route.fulfill(json(200, { ...ws, status: "paused" }));
  });
  await page.route("**/api/grove/workspaces/w-grove-1/resume", (route) => {
    ref.status = "active";
    return route.fulfill(json(200, { ...ws, status: "active" }));
  });
  await page.route("**/api/grove/workspaces/w-grove-1/kill", (route) => {
    ref.killed = true;
    return route.fulfill({ status: 204 });
  });
  await page.route("**/api/grove/workspaces/w-grove-1/commits", (route) =>
    route.fulfill(json(200, [])),
  );
  await page.route("**/api/grove/workspaces/w-grove-1/sessions**", (route) =>
    route.fulfill(json(200, [])),
  );
}

test.describe("lifecycle controls", () => {
  test("pauses then resumes a running workspace", async ({ page }) => {
    await mockWorkspace(page, { status: "active" });
    await page.goto("/w/w-grove-1");
    const actions = page.getByTestId("workspace-actions");
    await expect(actions.getByTestId("action-pause")).toBeVisible();

    await actions.getByTestId("action-pause").click();
    await expect(actions.getByTestId("action-resume")).toBeVisible();
    await expect(actions.getByTestId("action-pause")).toHaveCount(0);

    await actions.getByTestId("action-resume").click();
    await expect(actions.getByTestId("action-pause")).toBeVisible();
  });

  test("kill confirm defaults to deleting a grove branch and returns home", async ({ page }) => {
    await mockWorkspace(page, { status: "active", provenance: "grove" });
    await page.goto("/w/w-grove-1");

    await page.getByTestId("action-kill").click();
    const confirm = page.getByTestId("kill-confirm-dialog");
    await expect(confirm).toBeVisible();
    await expect(confirm.getByTestId("kill-delete-branch")).toBeChecked();

    await confirm.getByTestId("kill-confirm").click();
    await expect(page).toHaveURL(/\/$/);
  });

  test("root workspace hides pause and never deletes the branch", async ({ page }) => {
    await mockWorkspace(page, { status: "active", placement: "root", provenance: "attached" });
    await page.goto("/w/w-grove-1");
    const actions = page.getByTestId("workspace-actions");
    await expect(actions.getByTestId("action-kill")).toBeVisible();
    await expect(actions.getByTestId("action-pause")).toHaveCount(0);

    await actions.getByTestId("action-kill").click();
    const checkbox = page.getByTestId("kill-delete-branch");
    await expect(checkbox).not.toBeChecked();
    await expect(checkbox).toBeDisabled();
  });
});
