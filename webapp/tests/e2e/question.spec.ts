import { test, expect } from "@playwright/test";

// A pending `AskUserQuestion` GROUP must appear the moment it's asked —
// pushed over the SAME `/events` connection the chat
// panel already holds — and be answerable from the page. `questions` is a
// LIST (a real batch can carry more than one, answered atomically in one
// POST — research-findings.md's Color/Toppings tab-bar case). The fake
// daemon's `/_test/push-event` control route (test-harness only, see
// _fake-daemon.ts) simulates the daemon's hook-sourced live signal;
// `w-grove-1` is the fixtures' active workspace, whose head session is
// `s-w-grove-1` (buildSessions).

const FAKE_DAEMON_URL = "http://127.0.0.1:8421";

const QUESTION = {
  id: "toolu_e2e#0",
  group_id: "toolu_e2e",
  kind: "single_select",
  prompt: "Ship to production now?",
  header: "Live pending question",
  options: [
    { label: "Ship now", description: "Deploy immediately" },
    { label: "Wait for review", description: "Hold for a second pair of eyes" },
  ],
  multiselect: false,
  answered: false,
  answer: null,
  source_tool: "AskUserQuestion",
};

const TOPPINGS = {
  id: "toolu_e2e#1",
  group_id: "toolu_e2e",
  kind: "multi_select",
  prompt: "Which environments?",
  header: null,
  options: [
    { label: "Staging", description: null },
    { label: "Production", description: null },
  ],
  multiselect: true,
  answered: false,
  answer: null,
  source_tool: "AskUserQuestion",
};

test("a pending question appears live via the stream, gets answered, and the card resolves", async ({
  page,
  request,
}) => {
  await page.goto("/w/w-grove-1");
  await expect(page.getByTestId("chat-panel")).toBeVisible();
  await expect(page.getByTestId("pending-question-card")).toHaveCount(0);

  await request.post(`${FAKE_DAEMON_URL}/_test/push-event`, {
    data: { workspaceId: "w-grove-1", questions: [QUESTION] },
  });

  const card = page.getByTestId("pending-question-card");
  await expect(card).toBeVisible();
  await expect(card).toContainText("Ship to production now?");
  await expect(card).toContainText("Wait for review");

  const posted = page.waitForRequest(
    (r) =>
      r.method() === "POST" &&
      r.url().includes("/api/grove/workspaces/w-grove-1/question-answer"),
  );
  await card.getByTestId("question-option-button").first().click();

  const req = await posted;
  expect(req.postDataJSON()).toEqual({
    session_id: "s-w-grove-1",
    tool_use_id: "toolu_e2e",
    answers: [{ selected_indexes: [0] }],
  });

  // The daemon resolves it (PostToolUse) — the stream carries `questions: []`
  // next, and the synthetic live card leaves pending mode on its own.
  await request.post(`${FAKE_DAEMON_URL}/_test/push-event`, {
    data: { workspaceId: "w-grove-1", questions: [] },
  });
  await expect(page.getByTestId("pending-question-card")).toHaveCount(0);
});

test("a genuine multi-question batch renders as ONE group and answers all, then one submit", async ({
  page,
  request,
}) => {
  await page.goto("/w/w-grove-1");
  await expect(page.getByTestId("chat-panel")).toBeVisible();

  await request.post(`${FAKE_DAEMON_URL}/_test/push-event`, {
    data: { workspaceId: "w-grove-1", questions: [QUESTION, TOPPINGS] },
  });

  const card = page.getByTestId("pending-question-card");
  await expect(card).toBeVisible();
  await expect(card).toContainText("Ship to production now?");
  await expect(card).toContainText("Which environments?");
  // ONE group card for both questions, not two.
  await expect(page.getByTestId("pending-question-card")).toHaveCount(1);

  const submit = card.getByTestId("question-submit");
  await expect(submit).toBeDisabled();

  await card.getByTestId("question-option-button").first().click(); // "Ship now"
  const checkboxes = card.getByTestId("question-checkbox");
  await checkboxes.nth(1).click(); // "Production"
  await expect(submit).toBeEnabled();

  const posted = page.waitForRequest(
    (r) =>
      r.method() === "POST" &&
      r.url().includes("/api/grove/workspaces/w-grove-1/question-answer"),
  );
  await submit.click();

  const req = await posted;
  expect(req.postDataJSON()).toEqual({
    session_id: "s-w-grove-1",
    tool_use_id: "toolu_e2e",
    answers: [{ selected_indexes: [0] }, { selected_indexes: [1] }],
  });

  await request.post(`${FAKE_DAEMON_URL}/_test/push-event`, {
    data: { workspaceId: "w-grove-1", questions: [] },
  });
  await expect(page.getByTestId("pending-question-card")).toHaveCount(0);
});

test("a stale answer is refused (409) and the card falls back to read-only pending", async ({
  page,
  request,
}) => {
  await page.goto("/w/w-grove-1");
  await expect(page.getByTestId("chat-panel")).toBeVisible();

  await request.post(`${FAKE_DAEMON_URL}/_test/push-event`, {
    data: { workspaceId: "w-grove-1", questions: [QUESTION] },
  });
  const card = page.getByTestId("pending-question-card");
  await expect(card).toBeVisible();

  // Model the race the 409 exists for: the daemon moves on from this
  // tool_use_id (resolved elsewhere, or superseded) faster than the SSE
  // delta reaches this page, so the still-rendered card answers a
  // tool_use_id the daemon no longer accepts.
  await request.post(`${FAKE_DAEMON_URL}/_test/set-pending-tool-use-id`, {
    data: { toolUseId: "toolu_moved_on" },
  });

  await card.getByTestId("question-option-button").first().click();
  await expect(page.getByTestId("question-error")).toContainText("Steering unavailable");
  // Degraded to the exact read-only card — no interactive controls survive.
  // (Scoped by text: the fixture's canned /turns history already has an
  // unrelated unanswered `question-card` of its own on this page.)
  const degraded = page.getByTestId("question-card").filter({ hasText: "Ship to production now?" });
  await expect(degraded).toBeVisible();
  await expect(degraded.getByTestId("question-option-button")).toHaveCount(0);
});
