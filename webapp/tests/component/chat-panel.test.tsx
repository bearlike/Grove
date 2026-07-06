import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

// Session selection + the activity snapshot are page-owned since #130, so the
// panel is a pure renderer: it takes the resolved `sessionId`, the activity
// `snapshot` (source of live pending questions), and the selected session's
// `agentState`. These tests exercise that prop contract — transcript,
// notifications, and the live pending-question flow — driving question changes
// by re-rendering with a new snapshot rather than polling `/activity`. The
// session-picker cascade + remap now live at the page; their contract is pinned
// by `session-picker.test.tsx` (the control) + `chat.spec.ts` (the integration).

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

/** Route the panel's one read (the turns digest) by URL. */
function stubTurns() {
  vi.stubGlobal("fetch", vi.fn(async (url: RequestInfo | URL) => turnsRouter(url)));
}

const json = (body: unknown) =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });

async function turnsRouter(url: RequestInfo | URL): Promise<Response> {
  const u = String(url);
  if (u.includes("/turns")) return json(DETAIL);
  throw new Error(`unexpected fetch ${u}`);
}

/** `stubTurns` plus routing for the answer POST (the live pending-question
 *  flow). The pending group itself arrives via the `snapshot` PROP, not a
 *  fetch — so no `/activity` routing is needed here anymore. */
function stubAnswer(answerResponse: Response = new Response(null, { status: 204 })) {
  const answerCalls: unknown[] = [];
  const fetchMock = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const u = String(url);
    if (u.includes("/question-answer")) {
      answerCalls.push(init?.body ? JSON.parse(String(init.body)) : undefined);
      return answerResponse;
    }
    return turnsRouter(url);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, answerCalls };
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

/** One-workspace `DashboardSnapshotView` carrying a live pending question GROUP
 * on `w1`'s session `s1` — the page reads exactly this off `useActivityStream`
 * and hands it down as the `snapshot` prop. `state` flips to "blocked" while a
 * group is pending so the fixture stays coherent with the questions it holds. */
function snapshotWithQuestions(questions: AgentQuestionView[]): DashboardSnapshotView {
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

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

/** Render the panel as the page would: the resolved session `s1` + a snapshot. */
function renderPanel(
  {
    snapshot = null,
    agentState = "idle",
  }: { snapshot?: DashboardSnapshotView | null; agentState?: AgentActivityView["state"] } = {},
  qc = makeClient(),
) {
  return render(
    <QueryClientProvider client={qc}>
      <ChatPanel workspaceId="w1" sessionId="s1" snapshot={snapshot} agentState={agentState} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

describe("ChatPanel", () => {
  it("tags each bubble with an IRC-style speaker label: you (clay) / agent (blue)", async () => {
    stubTurns();
    renderPanel();

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
    stubTurns();
    renderPanel();

    const note = await screen.findByTestId("chat-notification");
    expect(note).toHaveTextContent("Background task completed: Explore");
    // Not a bubble, not a plain note — its own seam.
    expect(note.querySelector('[data-testid="chat-message"]')).toBeNull();
    expect(note).not.toHaveTextContent("The full subagent result body.");

    fireEvent.click(note.querySelector("button")!);
    expect(note).toHaveTextContent("The full subagent result body.");
  });

  it("clamps the user bubble by default, keeps the full text in the DOM, and hides the toggle without layout (jsdom)", async () => {
    // #128's SmartCollapse contract, ported to the #130 prop-driven panel.
    stubTurns();
    renderPanel();

    const messages = await screen.findAllByTestId("chat-message");
    const user = messages.find((m) => m.dataset.role === "user")!;
    expect(user).toBeDefined();

    // The full prompt is always mounted — collapse is visual, never a truncation.
    expect(user).toHaveTextContent("ship it");

    // The clamp wrapper carries the collapse seam and is collapsed by default.
    const clamp = within(user).getByTestId("user-message-collapse");
    expect(clamp).toHaveAttribute("data-collapsed", "true");

    // jsdom has no layout (scrollHeight 0), so nothing overflows → no toggle.
    expect(within(user).queryByTestId("user-message-toggle")).toBeNull();
  });

  it("renders a live pending question from the snapshot (not /turns) as an interactive card", async () => {
    stubTurns();
    renderPanel({ snapshot: snapshotWithQuestions([QUESTION]), agentState: "blocked" });

    const card = await screen.findByTestId("pending-question-card");
    expect(card).toHaveTextContent("Which migration strategy?");
    expect(screen.getAllByTestId("question-option-button")).toHaveLength(2);
  });

  it("answering a lone single-select question POSTs the plan to /question-answer", async () => {
    const { answerCalls } = stubAnswer();
    const user = userEvent.setup();
    renderPanel({ snapshot: snapshotWithQuestions([QUESTION]), agentState: "blocked" });

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
    const { answerCalls } = stubAnswer();
    const user = userEvent.setup();
    renderPanel({ snapshot: snapshotWithQuestions([QUESTION, TOPPINGS]), agentState: "blocked" });

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
    stubAnswer(refusal);
    const user = userEvent.setup();
    renderPanel({ snapshot: snapshotWithQuestions([QUESTION]), agentState: "blocked" });

    const buttons = await screen.findAllByTestId("question-option-button");
    await user.click(buttons[0]);

    const error = await screen.findByTestId("question-error");
    expect(error).toHaveTextContent("Steering unavailable");
    expect(screen.getByTestId("question-card")).toBeInTheDocument();
    expect(screen.queryByTestId("question-option-button")).toBeNull();
  });

  it("renders nothing extra when the snapshot reports no pending questions", async () => {
    stubTurns();
    renderPanel({ snapshot: snapshotWithQuestions([]), agentState: "working" });

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
    stubAnswer();
    const qc = makeClient();
    const user = userEvent.setup();
    const view = renderPanel({ snapshot: snapshotWithQuestions([QUESTION]), agentState: "blocked" }, qc);

    const buttons = await screen.findAllByTestId("question-option-button");
    await user.click(buttons[0]); // lone single-select auto-submits

    // The 204 resolved (isPending cleared), but the snapshot still reports the
    // SAME group — controls must stay disabled (isSuccess latches `submitting`),
    // not silently re-enable for a beat.
    await waitFor(() => {
      const stillButtons = screen.getAllByTestId("question-option-button");
      expect(stillButtons[0]).toBeDisabled();
    });

    // The page's next snapshot names the NEXT group (the poll noticing the old
    // one resolved) — the effect resets the mutation on `liveGroupId` change, so
    // the new group's controls come back enabled.
    view.rerender(
      <QueryClientProvider client={qc}>
        <ChatPanel
          workspaceId="w1"
          sessionId="s1"
          snapshot={snapshotWithQuestions([NEXT_QUESTION])}
          agentState="blocked"
        />
      </QueryClientProvider>,
    );

    await waitFor(
      () => {
        expect(screen.getByTestId("pending-question-card")).toHaveTextContent("Ship now?");
        expect(screen.getAllByTestId("question-option-button")[0]).not.toBeDisabled();
      },
      { timeout: 3000 },
    );
  });

  // The empty-state seam (#132): with no tracked session the panel shows an
  // empty state — generic by default, but a "track a session" CTA when the page
  // hands down a picker (its presence IS the "candidates exist" signal).
  it("keeps the generic empty state when nothing is tracked and no candidates exist", () => {
    render(
      <QueryClientProvider client={makeClient()}>
        <ChatPanel workspaceId="w1" sessionId={null} snapshot={null} agentState="idle" />
      </QueryClientProvider>,
    );
    expect(screen.getByText("No conversation yet")).toBeInTheDocument();
    expect(screen.getByText("Send a message to steer the agent.")).toBeInTheDocument();
    expect(screen.queryByText("No session tracked")).toBeNull();
  });

  it("swaps in the track-a-session CTA when candidates exist but none is tracked (#132)", () => {
    render(
      <QueryClientProvider client={makeClient()}>
        <ChatPanel
          workspaceId="w1"
          sessionId={null}
          snapshot={null}
          agentState="idle"
          emptyStatePicker={<div data-testid="stub-track-picker">picker</div>}
        />
      </QueryClientProvider>,
    );
    // Distinct calm copy + the picker mounted right in the empty state; the
    // generic "send a message" copy is gone.
    expect(screen.getByText("No session tracked")).toBeInTheDocument();
    expect(screen.getByText("Pick the session Grove should follow.")).toBeInTheDocument();
    expect(screen.getByTestId("stub-track-picker")).toBeInTheDocument();
    expect(screen.queryByText("No conversation yet")).toBeNull();
  });
});
