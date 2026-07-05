import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ChatPanel } from "@/components/chat/chat-panel";
import type {
  AgentActivityView,
  AgentQuestionView,
  DashboardSnapshotView,
  SessionDetailView,
  SessionSummaryView,
} from "@/lib/grove/types";

const SESSION: SessionSummaryView = {
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
  last_prompt: "ship it",
  activity: {
    state: "idle",
    title: null,
    current_task: null,
    human_turns: 1,
    assistant_replies: 1,
    replies_per_turn: [1],
    tool_calls: 0,
    active_subagents: 0,
    model: "claude-opus-4-8",
    tokens_in: 100,
    tokens_out: 10,
    last_event_at: null,
    needs_attention: false,
    error_detail: null,
    questions: [],
  },
};

const DETAIL: SessionDetailView = {
  session: SESSION,
  turns: [
    {
      user_text: "ship it",
      started_at: "2026-06-10T10:00:00Z",
      entries: [
        { role: "assistant", text: "Shipping the panel now." },
        {
          role: "notification",
          text: "Background task completed: Explore\nThe full subagent result body.",
        },
      ],
    },
  ],
};

/** Route the panel's two reads by URL — sessions list, then the turns digest. */
function stubFetch() {
  const json = (body: unknown) =>
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: RequestInfo | URL) => {
      const u = String(url);
      if (u.includes("/turns")) return json(DETAIL);
      if (u.includes("/sessions")) return json([SESSION]);
      throw new Error(`unexpected fetch ${u}`);
    }),
  );
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

/** One-workspace/one-session `DashboardSnapshotView` carrying a live pending
 * question GROUP on `w1`'s `s1` session — jsdom has no `EventSource`, so
 * `useActivityStream` falls back to polling `/activity`, which is how the
 * chat panel is meant to learn about a pending group in these tests. */
function activitySnapshotWithQuestions(questions: AgentQuestionView[]): DashboardSnapshotView {
  const activity: AgentActivityView = {
    state: questions.length > 0 ? "blocked" : "working",
    title: null,
    current_task: null,
    human_turns: 1,
    assistant_replies: 1,
    replies_per_turn: [1],
    tool_calls: 0,
    active_subagents: 0,
    model: null,
    tokens_in: 0,
    tokens_out: 0,
    last_event_at: null,
    needs_attention: questions.length > 0,
    error_detail: null,
    questions,
  };
  return {
    projects: [
      {
        repo_root: "/r",
        repo_name: "r",
        cwd: "/r",
        workspaces: [
          {
            state: { id: "w1" } as DashboardSnapshotView["projects"][number]["workspaces"][number]["state"],
            recent_commits: [],
            observed_at: "2026-06-10T12:00:00Z",
            sessions: [{ session: { session_id: "s1", adapter_kind: "claude_code", provenance: "grove_launched", tmux_window: null }, activity }],
            base_ahead: 0,
            base_behind: 0,
            diff_added: 0,
            diff_removed: 0,
            dirty_files: 0,
            pane_target: null,
            needs_attention: questions.length > 0,
          },
        ],
      },
    ],
    generated_at: "2026-06-10T12:00:00Z",
    total_workspaces: 1,
    needs_attention: questions.length > 0 ? 1 : 0,
  };
}

/** `stubFetch` plus routing for the activity poll (live pending question
 * group) and the answer POST — kept separate from `stubFetch` so the
 * existing tests above stay exactly as they were (their background
 * `/activity` poll just fails silently, as today). */
function stubFetchWithActivity(
  questions: AgentQuestionView[],
  answerResponse: Response = new Response(null, { status: 204 }),
) {
  const json = (body: unknown) =>
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  const answerCalls: unknown[] = [];
  const fetchMock = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const u = String(url);
    if (u.includes("/question-answer")) {
      answerCalls.push(init?.body ? JSON.parse(String(init.body)) : undefined);
      return answerResponse;
    }
    if (u.includes("/turns")) return json(DETAIL);
    if (u.includes("/sessions")) return json([SESSION]);
    if (u.includes("/activity")) return json(activitySnapshotWithQuestions(questions));
    throw new Error(`unexpected fetch ${u}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, answerCalls };
}

function r(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>);
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

describe("ChatPanel", () => {
  it("tags each bubble with an IRC-style speaker label: you (clay) / agent (blue)", async () => {
    stubFetch();
    r(<ChatPanel workspaceId="w1" />);

    const messages = await screen.findAllByTestId("chat-message");
    const user = messages.find((m) => m.dataset.role === "user");
    const assistant = messages.find((m) => m.dataset.role === "assistant");
    // data-role continuity: both bubbles still carry the wire role.
    expect(user).toBeDefined();
    expect(assistant).toBeDefined();

    const userLabel = user!.querySelector('[data-testid="role-label"]')!;
    const agentLabel = assistant!.querySelector('[data-testid="role-label"]')!;
    expect(userLabel.textContent).toBe("you");
    expect(agentLabel.textContent).toBe("agent");

    // Same tokens as TurnsView and the TUI (class-based — jsdom can't
    // resolve `var()`/theme utilities, so the class is the seam).
    expect(userLabel.className).toContain("text-primary");
    expect(agentLabel.className).toContain("text-[var(--ref-info)]");
    // The user label rides the right-aligned bubble; the agent label leads.
    expect(userLabel.className).toContain("self-end");
    expect(agentLabel.className).toContain("self-start");
  });

  it("renders a notification as a quiet row: summary visible, result behind a disclosure", async () => {
    stubFetch();
    r(<ChatPanel workspaceId="w1" />);

    const note = await screen.findByTestId("chat-notification");
    expect(note).toHaveTextContent("Background task completed: Explore");
    // Not a bubble, not a plain note — its own seam.
    expect(note.querySelector('[data-testid="chat-message"]')).toBeNull();
    expect(note).not.toHaveTextContent("The full subagent result body.");

    fireEvent.click(note.querySelector("button")!);
    expect(note).toHaveTextContent("The full subagent result body.");
  });

  it("renders a live pending question from the activity stream (not /turns) as an interactive card", async () => {
    stubFetchWithActivity([QUESTION]);
    r(<ChatPanel workspaceId="w1" />);

    const card = await screen.findByTestId("pending-question-card");
    expect(card).toHaveTextContent("Which migration strategy?");
    expect(screen.getAllByTestId("question-option-button")).toHaveLength(2);
  });

  it("answering a lone single-select question POSTs the plan to /question-answer", async () => {
    const { answerCalls } = stubFetchWithActivity([QUESTION]);
    const user = userEvent.setup();
    r(<ChatPanel workspaceId="w1" />);

    const buttons = await screen.findAllByTestId("question-option-button");
    await user.click(buttons[1]);

    await waitFor(() => expect(answerCalls).toHaveLength(1));
    expect(answerCalls[0]).toEqual({
      session_id: "s1",
      tool_use_id: "toolu_1",
      answers: [{ selected_indexes: [1] }],
    });
  });

  it("a genuine multi-question batch renders as ONE group and submits ONE POST with N ordered answers", async () => {
    const { answerCalls } = stubFetchWithActivity([QUESTION, TOPPINGS]);
    const user = userEvent.setup();
    r(<ChatPanel workspaceId="w1" />);

    const card = await screen.findByTestId("pending-question-card");
    expect(card).toHaveTextContent("Which migration strategy?");
    expect(card).toHaveTextContent("Toppings?");

    // Only one pending-question-card for the whole group, not two.
    expect(screen.getAllByTestId("pending-question-card")).toHaveLength(1);

    const submit = screen.getByTestId("question-submit");
    expect(submit).toBeDisabled();

    const optionButtons = screen.getAllByTestId("question-option-button");
    await user.click(optionButtons[1]); // "Incremental"
    const checkboxes = screen.getAllByTestId("question-checkbox");
    await user.click(checkboxes[0]); // "Cheese"

    expect(answerCalls).toHaveLength(0); // neither tap auto-submits a group
    await user.click(submit);

    await waitFor(() => expect(answerCalls).toHaveLength(1));
    expect(answerCalls[0]).toEqual({
      session_id: "s1",
      tool_use_id: "toolu_1",
      answers: [{ selected_indexes: [1] }, { selected_indexes: [0] }],
    });
  });

  it("a 409 refusal surfaces inline and the card falls back to read-only pending", async () => {
    const refusal = new Response(
      JSON.stringify({ detail: { error: "question_not_pending", message: "already resolved" } }),
      { status: 409, headers: { "content-type": "application/json" } },
    );
    stubFetchWithActivity([QUESTION], refusal);
    const user = userEvent.setup();
    r(<ChatPanel workspaceId="w1" />);

    const buttons = await screen.findAllByTestId("question-option-button");
    await user.click(buttons[0]);

    const error = await screen.findByTestId("question-error");
    expect(error).toHaveTextContent("Steering unavailable");
    expect(screen.getByTestId("question-card")).toBeInTheDocument();
    expect(screen.queryByTestId("question-option-button")).toBeNull();
  });

  it("renders nothing extra when the activity stream reports no pending questions", async () => {
    stubFetchWithActivity([]);
    r(<ChatPanel workspaceId="w1" />);

    await screen.findAllByTestId("chat-message");
    expect(screen.queryByTestId("pending-question-card")).toBeNull();
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
        if (u.includes("/sessions")) return json([SESSION]);
        // The activity poll reports the ORIGINAL group until a fresh read is
        // forced below — that's the "still the same pending group after a
        // resolved POST" window the latch must survive.
        if (u.includes("/activity")) {
          return json(activitySnapshotWithQuestions([answered ? NEXT_QUESTION : QUESTION]));
        }
        throw new Error(`unexpected fetch ${u}`);
      }),
    );
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={qc}>
        <ChatPanel workspaceId="w1" />
      </QueryClientProvider>,
    );

    const buttons = await screen.findAllByTestId("question-option-button");
    await user.click(buttons[0]); // lone single-select auto-submits
    await waitFor(() => expect(answerCalls).toHaveLength(1));

    // The 204 resolved (isPending cleared), but the stream hasn't reported a
    // new group yet — controls must stay disabled (isSuccess latches
    // `submitting`), not silently re-enable for a beat.
    await waitFor(() => {
      const stillButtons = screen.getAllByTestId("question-option-button");
      expect(stillButtons[0]).toBeDisabled();
    });

    // Force the next activity read (mirrors the real poll noticing the group
    // resolved and moving to the next one) — the effect resets the mutation
    // on `liveGroupId` change, so the NEW group's controls come back enabled.
    await qc.invalidateQueries({ queryKey: ["activity"] });

    await waitFor(
      () => {
        expect(screen.getByTestId("pending-question-card")).toHaveTextContent("Ship now?");
        expect(screen.getAllByTestId("question-option-button")[0]).not.toBeDisabled();
      },
      { timeout: 3000 },
    );
  });
});
