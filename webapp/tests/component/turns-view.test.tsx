import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TurnsView } from "@/components/workspace/turns-view";
import { snapshot, workspace } from "@/tests/_helpers/activity-fixtures";
import type { AgentQuestionView, SessionDetailView } from "@/lib/grove/types";

const DETAIL: SessionDetailView = {
  session: {
    session_id: "s1",
    adapter_kind: "claude_code",
    provenance: "grove_launched",
    workspace_id: "w1",
    workspace_title: "feat depth",
    workspace_branch: "feat/depth",
    git_branch: "feat/depth",
    created_at: "2026-06-10T10:00:00Z",
    modified_at: "2026-06-10T11:00:00Z",
    size_bytes: 4096,
    title: "wire the panel",
    first_prompt: "build the panel",
    last_prompt: "run the tests",
    activity: {
      state: "working",
      title: "ai: wiring",
      current_task: null,
      human_turns: 3,
      assistant_replies: 7,
      replies_per_turn: [3, 2, 2],
      tool_calls: 11,
      active_subagents: 0,
      model: "claude-opus-4-8",
      tokens_in: 1500,
      tokens_out: 150,
      last_event_at: null,
      needs_attention: false,
      error_detail: null,
      questions: [],
    },
  },
  turns: [
    // A resumed-session head: no fresh prompt, just carried-over context.
    {
      user_text: "",
      started_at: "2026-06-10T09:00:00Z",
      entries: [{ role: "summary", text: "continued from a prior session" }],
    },
    {
      user_text: "build the panel",
      started_at: "2026-06-10T10:00:00Z",
      entries: [
        { role: "assistant", text: "Starting on the panel." },
        // A consecutive tool run — renders as ONE collapsed "2 tool calls" row.
        { role: "tool", text: "Edit sessions-panel.tsx" },
        { role: "tool", text: "Bash npm test" },
      ],
    },
    {
      user_text: "run the tests",
      started_at: "2026-06-10T11:00:00Z",
      entries: [
        // A digest-level user interjection — labeled `you` like the prompt.
        { role: "user", text: "and lint too" },
        { role: "status", text: "tests green" },
      ],
    },
    // A structured agent question (epic #74), unanswered: renders as a
    // read-only choice card with its prompt + both option labels + pending.
    {
      user_text: "pick a strategy",
      started_at: "2026-06-10T12:00:00Z",
      entries: [
        {
          role: "question",
          text: "Which migration strategy?",
          question: {
            id: "q-1",
            group_id: "g-1",
            kind: "single_select",
            prompt: "Which migration strategy?",
            header: "Decision needed",
            options: [
              { label: "Big-bang cutover", description: "Faster, riskier" },
              { label: "Incremental", description: "Slower, safer" },
            ],
            multiselect: false,
            answered: false,
            answer: null,
            source_tool: "AskUserQuestion",
          },
        },
      ],
    },
    // An answered question: renders the chosen answer behind a ✓ check.
    {
      user_text: "and the deploy window?",
      started_at: "2026-06-10T13:00:00Z",
      entries: [
        {
          role: "question",
          text: "When should we deploy?",
          question: {
            id: "q-2",
            group_id: "g-1",
            kind: "single_select",
            prompt: "When should we deploy?",
            header: null,
            options: [
              { label: "Now", description: null },
              { label: "Off-hours", description: null },
            ],
            multiselect: false,
            answered: true,
            answer: "Off-hours",
            source_tool: "AskUserQuestion",
          },
        },
      ],
    },
  ],
};

function stubFetch(detail: SessionDetailView) {
  const mock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(detail), {
      status: 200,
      headers: { "content-type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", mock);
  return mock;
}

const QUESTION: AgentQuestionView = {
  id: "toolu_1#0",
  group_id: "toolu_1",
  kind: "single_select",
  prompt: "Which migration strategy?",
  header: "Decision needed",
  options: [
    { label: "Big-bang cutover", description: "Faster, riskier" },
    { label: "Incremental", description: "Slower, safer" },
  ],
  multiselect: false,
  answered: false,
  answer: null,
  source_tool: "AskUserQuestion",
};

/** The shared `workspace()` fixture derives `session_id` from the workspace
 * id (`s-${id}`); TurnsView's own tests use `sessionId="s1"` directly, so the
 * one session_id is overridden to match after building. */
function snapshotWithQuestions(questions: AgentQuestionView[]) {
  const ws = workspace("w1", questions.length > 0 ? "blocked" : "working", undefined, questions);
  ws.sessions[0].session.session_id = "s1";
  return snapshot(ws);
}

/** Routes `/turns`, the `/activity` poll (jsdom has no `EventSource`, so
 * `useActivityStream` falls back to it — this is how TurnsView is meant to
 * learn about a live pending question group), and the answer POST. */
function stubFetchWithActivity(
  detail: SessionDetailView,
  questions: AgentQuestionView[],
  answerResponse: Response = new Response(null, { status: 204 }),
) {
  const json = (body: unknown) =>
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  const answerCalls: unknown[] = [];
  const mock = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const u = String(url);
    if (u.includes("/question-answer")) {
      answerCalls.push(init?.body ? JSON.parse(String(init.body)) : undefined);
      return answerResponse;
    }
    if (u.includes("/turns")) return json(detail);
    if (u.includes("/activity")) return json(snapshotWithQuestions(questions));
    throw new Error(`unexpected fetch ${u}`);
  });
  vi.stubGlobal("fetch", mock);
  return { mock, answerCalls };
}

function r(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>);
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

describe("TurnsView", () => {
  it("fetches last=100 and renders turns oldest-first", async () => {
    const mock = stubFetch(DETAIL);
    r(<TurnsView workspaceId="w1" sessionId="s1" />);

    const rows = await screen.findAllByTestId("turn-row");
    expect(mock).toHaveBeenCalledWith(
      "/api/grove/workspaces/w1/sessions/s1/turns?last=100",
      expect.objectContaining({ method: "GET" }),
    );
    expect(rows).toHaveLength(5);
    // Wire order preserved: oldest turn first, newest last.
    expect(rows[1]).toHaveTextContent("build the panel");
    expect(rows[2]).toHaveTextContent("run the tests");
  });

  it("renders an empty-prompt head as a quiet continued-session marker", async () => {
    stubFetch(DETAIL);
    r(<TurnsView workspaceId="w1" sessionId="s1" />);

    const rows = await screen.findAllByTestId("turn-row");
    expect(rows[0]).toHaveTextContent("continued session");
    expect(rows[0]).not.toHaveTextContent("❯");
  });

  it("collapses a consecutive tool run into one closed 'N tool calls' row", async () => {
    stubFetch(DETAIL);
    r(<TurnsView workspaceId="w1" sessionId="s1" />);

    await screen.findAllByTestId("turn-row");
    const group = screen.getByTestId("tool-group");
    expect(group).toHaveTextContent("2 tool calls");

    // Collapsed by default: the disclosure button reads closed and no
    // individual tool entries are mounted.
    const toggle = group.querySelector("button")!;
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    const entries = screen.getAllByTestId("turn-entry");
    expect(entries.some((e) => e.dataset.role === "tool")).toBe(false);
  });

  it("expanding a tool group reveals the individual mono ⚒ calls", async () => {
    stubFetch(DETAIL);
    const user = userEvent.setup();
    r(<TurnsView workspaceId="w1" sessionId="s1" />);

    await screen.findAllByTestId("turn-row");
    const toggle = screen.getByTestId("tool-group").querySelector("button")!;
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");

    const tools = screen
      .getAllByTestId("turn-entry")
      .filter((e) => e.dataset.role === "tool");
    expect(tools).toHaveLength(2);
    // The tool concept is the shared Wrench glyph (one per concept) — a lucide
    // icon renders as an inline <svg>, replacing the old ⚒ text marker.
    expect(tools[0].querySelector("svg")).not.toBeNull();
    expect(tools[0]).toHaveTextContent("Edit sessions-panel.tsx");
    expect(tools[1]).toHaveTextContent("Bash npm test");
    expect(tools[0].className).toContain("font-mono");

    // Re-click collapses again.
    await user.click(toggle);
    expect(
      screen.getAllByTestId("turn-entry").some((e) => e.dataset.role === "tool"),
    ).toBe(false);
  });

  it("styles non-tool entries by role", async () => {
    stubFetch(DETAIL);
    r(<TurnsView workspaceId="w1" sessionId="s1" />);

    await screen.findAllByTestId("turn-row");
    const entries = screen.getAllByTestId("turn-entry");
    const assistant = entries.find((e) => e.dataset.role === "assistant");
    expect(assistant).toBeDefined();
    expect(assistant!.className).not.toContain("font-mono");
  });

  it("labels speakers IRC-style: you (clay) vs agent (blue)", async () => {
    stubFetch(DETAIL);
    r(<TurnsView workspaceId="w1" sessionId="s1" />);

    const rows = await screen.findAllByTestId("turn-row");
    // The continued-session head has no speaker, so no label.
    expect(rows[0].querySelector('[data-testid="role-label"]')).toBeNull();

    const labels = screen.getAllByTestId("role-label");
    const you = labels.filter((l) => l.dataset.roleLabel === "you");
    const agent = labels.filter((l) => l.dataset.roleLabel === "agent");
    expect(you).toHaveLength(5); // four ❯ prompts + one digest user entry
    expect(agent).toHaveLength(1); // one assistant entry
    expect(you[0].textContent).toBe("you");
    expect(agent[0].textContent).toBe("agent");

    // Colors ride existing tokens (shared convention with the TUI): user =
    // the clay accent (NOT branch teal — branch names sit in teal on the row
    // above), agent = the agent-identity blue. Class-based tokens, so the
    // seam is the class — jsdom can't resolve `var()`/theme utilities, which
    // is why expectColor doesn't apply here.
    expect(you[0].className).toContain("text-primary");
    expect(agent[0].className).toContain("text-[var(--ref-info)]");

    // data-role continuity on the labeled entry rows is unchanged.
    const entries = screen.getAllByTestId("turn-entry");
    expect(entries.find((e) => e.dataset.role === "assistant")).toBeDefined();
    expect(entries.find((e) => e.dataset.role === "user")).toBeDefined();
  });

  it("renders an unanswered question as a read-only card: prompt + both options + pending", async () => {
    stubFetch(DETAIL);
    r(<TurnsView workspaceId="w1" sessionId="s1" />);

    await screen.findAllByTestId("turn-row");
    const cards = screen.getAllByTestId("question-card");
    // Two questions in the fixture: the unanswered one is pending.
    const pending = cards.find((c) => c.dataset.answered === "false")!;
    expect(pending).toBeDefined();
    expect(pending).toHaveTextContent("Which migration strategy?");

    const options = pending.querySelectorAll('[data-testid="question-option"]');
    expect(options).toHaveLength(2);
    expect(options[0]).toHaveTextContent("Big-bang cutover");
    expect(options[0]).toHaveTextContent("Faster, riskier");
    expect(options[1]).toHaveTextContent("Incremental");

    // Read-only: options are a static list, never interactive controls.
    expect(pending.querySelector("button")).toBeNull();
    expect(pending.querySelector("input")).toBeNull();

    expect(pending.querySelector('[data-testid="question-pending"]')).toHaveTextContent(
      "awaiting answer",
    );
    expect(pending.querySelector('[data-testid="question-answer"]')).toBeNull();
  });

  it("renders an answered question with its answer and a check, no pending note", async () => {
    stubFetch(DETAIL);
    r(<TurnsView workspaceId="w1" sessionId="s1" />);

    await screen.findAllByTestId("turn-row");
    const cards = screen.getAllByTestId("question-card");
    const answered = cards.find((c) => c.dataset.answered === "true")!;
    expect(answered).toBeDefined();
    expect(answered).toHaveTextContent("When should we deploy?");

    const answer = answered.querySelector('[data-testid="question-answer"]')!;
    expect(answer).toHaveTextContent("Off-hours");
    // The ✓ check is the answered cue — a lucide icon is an inline <svg>.
    expect(answer.querySelector("svg")).not.toBeNull();
    expect(answered.querySelector('[data-testid="question-pending"]')).toBeNull();

    // The `turn-entry` test hook survives, tagged with the question role.
    const questionEntries = screen
      .getAllByTestId("turn-entry")
      .filter((e) => e.dataset.role === "question");
    expect(questionEntries).toHaveLength(2);
  });

  it("shows the error state when the turns fetch fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("boom")));
    r(<TurnsView workspaceId="w1" sessionId="s1" />);

    expect(await screen.findByText("couldn't load turns")).toBeInTheDocument();
  });

  it("renders a live pending question from the activity stream alongside the loaded turns", async () => {
    stubFetchWithActivity(DETAIL, [QUESTION]);
    r(<TurnsView workspaceId="w1" sessionId="s1" />);

    await screen.findAllByTestId("turn-row");
    const live = await screen.findByTestId("turns-live-question");
    expect(live).toHaveTextContent("Which migration strategy?");
    expect(live.querySelectorAll('[data-testid="question-option-button"]')).toHaveLength(2);
  });

  it("answering the live question POSTs the plan to /question-answer", async () => {
    const { answerCalls } = stubFetchWithActivity(DETAIL, [QUESTION]);
    const user = userEvent.setup();
    r(<TurnsView workspaceId="w1" sessionId="s1" />);

    const live = await screen.findByTestId("turns-live-question");
    const buttons = live.querySelectorAll('[data-testid="question-option-button"]');
    await user.click(buttons[0]);

    await waitFor(() => expect(answerCalls).toHaveLength(1));
    expect(answerCalls[0]).toEqual({
      session_id: "s1",
      tool_use_id: "toolu_1",
      answers: [{ selected_indexes: [0] }],
    });
  });

  it("a genuine multi-question batch renders as one group and answers all, then one submit", async () => {
    const TOPPINGS: AgentQuestionView = {
      id: "toolu_1#1",
      group_id: "toolu_1",
      kind: "multi_select",
      prompt: "Toppings?",
      header: null,
      options: [
        { label: "Cheese", description: null },
        { label: "Mushrooms", description: null },
      ],
      multiselect: true,
      answered: false,
      answer: null,
      source_tool: "AskUserQuestion",
    };
    const { answerCalls } = stubFetchWithActivity(DETAIL, [QUESTION, TOPPINGS]);
    const user = userEvent.setup();
    r(<TurnsView workspaceId="w1" sessionId="s1" />);

    const live = await screen.findByTestId("turns-live-question");
    expect(live).toHaveTextContent("Which migration strategy?");
    expect(live).toHaveTextContent("Toppings?");
    expect(screen.getAllByTestId("pending-question-card")).toHaveLength(1);

    const optionButtons = live.querySelectorAll('[data-testid="question-option-button"]');
    await user.click(optionButtons[0]);
    const checkboxes = live.querySelectorAll('[data-testid="question-checkbox"]');
    await user.click(checkboxes[1]);
    expect(answerCalls).toHaveLength(0);

    await user.click(screen.getByTestId("question-submit"));
    await waitFor(() => expect(answerCalls).toHaveLength(1));
    expect(answerCalls[0]).toEqual({
      session_id: "s1",
      tool_use_id: "toolu_1",
      answers: [{ selected_indexes: [0] }, { selected_indexes: [1] }],
    });
  });

  it("does not render a live question row when the activity stream has none pending", async () => {
    stubFetchWithActivity(DETAIL, []);
    r(<TurnsView workspaceId="w1" sessionId="s1" />);

    await screen.findAllByTestId("turn-row");
    expect(screen.queryByTestId("turns-live-question")).toBeNull();
  });

  it("latches disabled controls after a resolved 204 until a NEW question group arrives (#111)", async () => {
    const NEXT_QUESTION: AgentQuestionView = {
      id: "toolu_2#0",
      group_id: "toolu_2",
      kind: "single_select",
      prompt: "Ship now?",
      header: null,
      options: [{ label: "Yes", description: null }, { label: "No", description: null }],
      multiselect: false,
      answered: false,
      answer: null,
      source_tool: "AskUserQuestion",
    };
    const json = (body: unknown) =>
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    const answerCalls: unknown[] = [];
    let answered = false;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
        const u = String(url);
        if (u.includes("/question-answer")) {
          answerCalls.push(init?.body ? JSON.parse(String(init.body)) : undefined);
          answered = true;
          return new Response(null, { status: 204 });
        }
        if (u.includes("/turns")) return json(DETAIL);
        // The activity poll reports the ORIGINAL group until the next read —
        // the window the latch must survive without silently re-enabling.
        if (u.includes("/activity")) {
          return json(snapshotWithQuestions([answered ? NEXT_QUESTION : QUESTION]));
        }
        throw new Error(`unexpected fetch ${u}`);
      }),
    );
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={qc}>
        <TurnsView workspaceId="w1" sessionId="s1" />
      </QueryClientProvider>,
    );

    const live = await screen.findByTestId("turns-live-question");
    const buttons = live.querySelectorAll('[data-testid="question-option-button"]');
    await user.click(buttons[0]); // lone single-select auto-submits
    await waitFor(() => expect(answerCalls).toHaveLength(1));

    // The 204 resolved (isPending cleared), but the stream hasn't reported a
    // new group yet — controls must stay disabled (isSuccess latches
    // `submitting`), not silently re-enable for a beat.
    await waitFor(() => {
      const stillLive = screen.getByTestId("turns-live-question");
      const stillButtons = stillLive.querySelectorAll('[data-testid="question-option-button"]');
      expect(stillButtons[0]).toBeDisabled();
    });

    // Force the next activity read (mirrors the real poll noticing the group
    // resolved and moving to the next one) — the effect resets the mutation
    // on `liveGroupId` change, so the NEW group's controls come back enabled.
    await qc.invalidateQueries({ queryKey: ["activity"] });

    await waitFor(
      () => {
        const fresh = screen.getByTestId("turns-live-question");
        expect(fresh).toHaveTextContent("Ship now?");
        expect(fresh.querySelectorAll('[data-testid="question-option-button"]')[0]).not.toBeDisabled();
      },
      { timeout: 3000 },
    );
  });
});
