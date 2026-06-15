import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TurnsView } from "@/components/workspace/turns-view";
import type { SessionDetailView } from "@/lib/grove/types";

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
});
