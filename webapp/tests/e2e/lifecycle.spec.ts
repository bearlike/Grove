import { test, expect, type Page } from "@playwright/test";

// Workspace lifecycle parity (#56). The create flow rides the full cookie → BFF
// → fake-daemon chain (the create POST is non-destructive to the shared read
// fixtures). The stateful pause/resume/kill transitions are isolated per-test
// via page.route, so they never mutate the one shared fake daemon other specs
// read from.

test.describe("create workspace (composer-first)", () => {
  // The composer at the top of `/` IS the create surface (#96): type → Enter.
  // Title auto-derives from the first prompt line; the full prompt rides as the
  // initial_prompt. Agent + repo default from the first known project.

  async function waitReady(page: import("@playwright/test").Page) {
    // Defaults (repo → agent) resolve async; the send control enables once the
    // draft is valid, so wait on it before submitting.
    await expect(page.getByTestId("composer-send")).toBeEnabled();
  }

  test("type → Enter submits an auto branch plan", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("composer-prompt").fill("new feature");
    await waitReady(page);

    const reqPromise = page.waitForRequest(
      (r) => r.url().endsWith("/api/grove/workspaces") && r.method() === "POST",
    );
    await page.getByTestId("composer-prompt").press("Enter");
    const body = (await reqPromise).postDataJSON();
    expect(body).toMatchObject({
      agent_name: "claude",
      title: "new feature",
      repo_root: "/repos/Grove",
      branch_plan: { kind: "auto" },
      initial_prompt: "new feature",
    });
  });

  test("a selected model rides the request as model", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("composer-model").click();
    await page.getByRole("menuitem", { name: "claude-sonnet-4-6", exact: true }).click();
    await page.getByTestId("composer-prompt").fill("with a model");
    await waitReady(page);

    const reqPromise = page.waitForRequest(
      (r) => r.url().endsWith("/api/grove/workspaces") && r.method() === "POST",
    );
    await page.getByTestId("composer-prompt").press("Enter");
    const body = (await reqPromise).postDataJSON();
    expect(body).toMatchObject({ title: "with a model", model: "claude-sonnet-4-6" });
  });

  test("a custom model id rides the request", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("composer-model").click();
    await page.getByTestId("composer-model-custom").fill("my-custom-model");
    await page.getByTestId("composer-model-custom").press("Enter");
    await page.getByTestId("composer-prompt").fill("custom model run");
    await waitReady(page);

    const reqPromise = page.waitForRequest(
      (r) => r.url().endsWith("/api/grove/workspaces") && r.method() === "POST",
    );
    await page.getByTestId("composer-prompt").press("Enter");
    const body = (await reqPromise).postDataJSON();
    expect(body.model).toBe("my-custom-model");
  });

  test("Advanced builds a new-named branch plan", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("composer-advanced-toggle").click();
    await page.getByTestId("create-mode").selectOption("new_named");
    await page.getByTestId("create-new-name").fill("feature/x");
    await page.getByTestId("composer-prompt").fill("named branch");
    await waitReady(page);

    const reqPromise = page.waitForRequest(
      (r) => r.url().endsWith("/api/grove/workspaces") && r.method() === "POST",
    );
    await page.getByTestId("composer-prompt").press("Enter");
    const body = (await reqPromise).postDataJSON();
    expect(body.branch_plan).toMatchObject({ kind: "new_named", name: "feature/x" });
  });

  test("Advanced root mode auto-checks skip-init and explains itself", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("composer-advanced-toggle").click();
    await page.getByTestId("create-mode").selectOption("root");
    await expect(page.getByTestId("create-skip-init")).toBeChecked();
    await expect(page.getByTestId("create-root-note")).toBeVisible();
  });

  test("Advanced resume-session-id rides the request as resume_session_id (#121)", async ({
    page,
  }) => {
    await page.goto("/");
    await page.getByTestId("composer-advanced-toggle").click();
    await page.getByTestId("create-resume-session-id").fill("a1b2c3d4-adopt-me");
    await page.getByTestId("composer-prompt").fill("resume an existing session");
    await waitReady(page);

    const reqPromise = page.waitForRequest(
      (r) => r.url().endsWith("/api/grove/workspaces") && r.method() === "POST",
    );
    await page.getByTestId("composer-prompt").press("Enter");
    const body = (await reqPromise).postDataJSON();
    expect(body.resume_session_id).toBe("a1b2c3d4-adopt-me");
  });

  test("an empty resume-session-id sends null, not an empty string", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("composer-prompt").fill("fresh session, no resume");
    await waitReady(page);

    const reqPromise = page.waitForRequest(
      (r) => r.url().endsWith("/api/grove/workspaces") && r.method() === "POST",
    );
    await page.getByTestId("composer-prompt").press("Enter");
    const body = (await reqPromise).postDataJSON();
    expect(body.resume_session_id).toBeNull();
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
  // Every lifecycle verb lives in the identity popover again (#153) — the
  // separate "Actions" dropdown is gone. Open the popover via `identity-trigger`;
  // a plain popover button click does NOT dismiss the surface, so the reversible
  // verb swaps IN PLACE (pause → resume) without the popover closing.

  test("pauses then resumes a running workspace", async ({ page }) => {
    await mockWorkspace(page, { status: "active" });
    await page.goto("/w/w-grove-1");

    await page.getByTestId("identity-trigger").click();
    await page.getByTestId("action-pause").click();
    // The popover stays open across the mutation, so once the status flips to
    // paused the same open popover re-renders pause→resume live. The sessionless
    // identity badge on the trigger mirrors the status.
    await expect(page.getByTestId("status-badge")).toHaveAttribute("data-status", "paused");
    await expect(page.getByTestId("action-resume")).toBeVisible();
    await expect(page.getByTestId("action-pause")).toHaveCount(0);
    await page.getByTestId("action-resume").click();
    await expect(page.getByTestId("status-badge")).toHaveAttribute("data-status", "active");
  });

  test("kill confirm defaults to deleting a grove branch and returns home", async ({ page }) => {
    await mockWorkspace(page, { status: "active", provenance: "grove" });
    await page.goto("/w/w-grove-1");

    // Kill lives in the identity popover's danger zone now (#153).
    await page.getByTestId("identity-trigger").click();
    await page.getByTestId("action-kill").click();
    const confirm = page.getByTestId("kill-confirm-dialog");
    await expect(confirm).toBeVisible();
    await expect(confirm.getByTestId("kill-delete-branch")).toBeChecked();

    await confirm.getByTestId("kill-confirm").click();
    // Returns home (the peek would 404). Wait for the home composer — a definitive
    // nav-complete signal — before the URL, since on throttled mobile the
    // router.push view swap can outrun the location update `toHaveURL` polls.
    await expect(page.getByTestId("composer-prompt")).toBeVisible();
    await expect(page).toHaveURL(/\/$/, { timeout: 10_000 });
  });

  test("root workspace has no reversible verb and never deletes the branch", async ({ page }) => {
    await mockWorkspace(page, { status: "active", placement: "root", provenance: "attached" });
    await page.goto("/w/w-grove-1");

    await page.getByTestId("identity-trigger").click();
    // Root+active strips pause/resume, leaving only kill in the danger zone.
    await expect(page.getByTestId("action-kill")).toBeVisible();
    await expect(page.getByTestId("action-pause")).toHaveCount(0);
    await expect(page.getByTestId("action-resume")).toHaveCount(0);

    await page.getByTestId("action-kill").click();
    const checkbox = page.getByTestId("kill-delete-branch");
    await expect(checkbox).not.toBeChecked();
    await expect(checkbox).toBeDisabled();
  });
});
