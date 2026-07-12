import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { WorkPanel } from "@/components/workspace/work-panel";
import { AgentLiveStatus } from "@/lib/grove/agent-activity";
import type {
  AgentActivityView,
  CommitSummaryView,
  SessionActivityView,
  SessionDetailView,
  WorkspacePeekView,
  WorkspaceStateView,
} from "@/lib/grove/types";

// The tabbed panel replacing the once-bare TerminalPane (#142). Pins: Terminal
// is the default tab and keeps every seam TerminalPane already owns; Diff
// re-homes the stat trio + diff lines + commit list; Info re-homes the
// metrics line + identity + timestamps + the fleet tree (#174); full-screen
// toggles `aria-pressed`.

function stateView(overrides: Partial<WorkspaceStateView> = {}): WorkspaceStateView {
  return {
    id: "w1",
    title: "Build the thing",
    repo_root: "/repo",
    branch: "grove/build-the-thing",
    base_branch: "main",
    worktree_path: "/repo/.worktrees/build",
    tmux_session: "grove-build",
    agent_name: "claude",
    status: "active",
    created_at: "2026-07-01T10:00:00Z",
    updated_at: "2026-07-01T10:00:00Z",
    branch_provenance: "grove",
    placement: "worktree",
    ...overrides,
  } as WorkspaceStateView;
}

function peek(overrides: Partial<WorkspacePeekView> = {}): WorkspacePeekView {
  return {
    state: stateView(),
    base_ahead: 2,
    base_behind: 0,
    diff_added: 120,
    diff_removed: 40,
    dirty_files: 3,
    recent_commits: [],
    agent_snapshot: "$ pytest\n12 passed",
    snapshot_taken_at: "2026-07-06T10:00:00Z",
    ...overrides,
  } as WorkspacePeekView;
}

const COMMITS: CommitSummaryView[] = [
  { sha: "abc1234def5678", subject: "feat: wire the panel", committed_at: "2026-07-01T09:00:00Z" },
];

const WORKING = AgentLiveStatus.of({
  state: "working",
  human_turns: 4,
  tool_calls: 9,
  tokens_in: 100,
  tokens_out: 200,
  active_subagents: 2,
  model: "claude-sonnet-4-5",
} as AgentActivityView);

describe("WorkPanel", () => {
  it("defaults to the Terminal tab, rendering the existing TerminalPane", () => {
    render(<WorkPanel workspaceId="w1" peek={peek()} live={WORKING} commits={COMMITS} />);
    expect(screen.getByTestId("work-panel-tab-terminal")).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("terminal-pane")).toBeInTheDocument();
    expect(screen.getByTestId("peek-snapshot")).toHaveTextContent("pytest");
  });

  it("the Diff tab shows the stat trio, diff lines, and commit list", async () => {
    const user = userEvent.setup();
    render(<WorkPanel workspaceId="w1" peek={peek()} live={WORKING} commits={COMMITS} />);
    await user.click(screen.getByTestId("work-panel-tab-diff"));
    expect(screen.getByTestId("stat-trio")).toBeInTheDocument();
    expect(screen.getByText(/\+120/)).toBeInTheDocument();
    expect(screen.getByTestId("commit-list")).toBeInTheDocument();
    expect(screen.queryByTestId("terminal-pane")).not.toBeInTheDocument();
  });

  it("the Info tab shows the metrics line, subagent count, agent+model, and placement", async () => {
    const user = userEvent.setup();
    render(
      <WorkPanel
        workspaceId="w1"
        peek={peek({ state: stateView({ placement: "root" }) })}
        live={WORKING}
        commits={[]}
      />,
    );
    await user.click(screen.getByTestId("work-panel-tab-info"));
    expect(screen.getByTestId("metrics")).toHaveTextContent("4t");
    expect(screen.getByText(/2 bg agents/)).toBeInTheDocument();
    expect(screen.getByText("claude-sonnet-4-5")).toBeInTheDocument();
    expect(screen.getByTestId("placement-badge")).toBeInTheDocument();
  });

  it("the Info tab never renders the worktree path (host-private)", async () => {
    const user = userEvent.setup();
    render(<WorkPanel workspaceId="w1" peek={peek()} live={WORKING} commits={[]} />);
    await user.click(screen.getByTestId("work-panel-tab-info"));
    expect(screen.queryByText(/\/repo\/\.worktrees/)).toBeNull();
  });

  it("full screen toggles aria-pressed", async () => {
    const user = userEvent.setup();
    render(<WorkPanel workspaceId="w1" peek={peek()} live={WORKING} commits={[]} />);
    const toggle = screen.getByTestId("work-panel-fullscreen");
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-pressed", "true");
  });
});

// ─── fleet tree (#174) ────────────────────────────────────────────────────────

function fleetActivity(overrides: Partial<AgentActivityView> = {}): AgentActivityView {
  return {
    state: "waiting",
    title: null,
    current_task: null,
    human_turns: 0,
    assistant_replies: 0,
    replies_per_turn: [],
    tool_calls: 0,
    active_subagents: 0,
    model: null,
    tokens_in: 0,
    tokens_out: 0,
    last_event_at: null,
    needs_attention: false,
    error_detail: null,
    questions: [],
    ...overrides,
  };
}

const PRIMARY_AND_CHILD: SessionActivityView[] = [
  {
    session: {
      session_id: "s1",
      adapter_kind: "claude_code",
      provenance: "grove_launched",
      tmux_window: "agent",
      parent_session_id: null,
    },
    activity: fleetActivity({ state: "working", active_subagents: 1 }),
  },
  {
    session: {
      session_id: "a1",
      adapter_kind: "claude_code",
      provenance: "fs_discovered",
      tmux_window: null,
      parent_session_id: "s1",
    },
    activity: fleetActivity({
      title: "Explore",
      current_task: "Map the webapp directory",
      model: "claude-sonnet-4-5",
      human_turns: 2,
      tool_calls: 5,
    }),
  },
];

function renderWithQuery(ui: React.ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

describe("WorkPanel — fleet tree (#174)", () => {
  it("renders no tree chrome for a workspace with no itemized sub-agents", async () => {
    const user = userEvent.setup();
    render(<WorkPanel workspaceId="w1" peek={peek()} live={WORKING} commits={[]} sessions={[]} />);
    await user.click(screen.getByTestId("work-panel-tab-info"));
    expect(screen.queryByTestId("fleet-tree")).not.toBeInTheDocument();
    // Falls back to the bare count line (WORKING carries active_subagents: 2).
    expect(screen.getByText(/2 bg agents/)).toBeInTheDocument();
  });

  it("itemizes the parent/child link as an expandable tree labeled agentType/description/model", async () => {
    const user = userEvent.setup();
    render(
      <WorkPanel
        workspaceId="w1"
        peek={peek()}
        live={WORKING}
        commits={[]}
        sessions={PRIMARY_AND_CHILD}
      />,
    );
    await user.click(screen.getByTestId("work-panel-tab-info"));
    // The itemized tree wins over the bare count line.
    expect(screen.getByTestId("fleet-tree")).toBeInTheDocument();
    expect(screen.queryByText(/2 bg agents/)).not.toBeInTheDocument();

    await user.click(screen.getByText(/1 sub-agent/));
    const child = screen.getByTestId("fleet-child");
    expect(child).toHaveAttribute("data-session-id", "a1");
    expect(child).toHaveTextContent("Explore");
    expect(child).toHaveTextContent("Map the webapp directory");
    expect(child).toHaveTextContent("claude-sonnet-4-5");
  });

  it("fetches a sub-agent's own turns on demand when its row expands", async () => {
    const detail: SessionDetailView = {
      session: {
        session_id: "a1",
        adapter_kind: "claude_code",
        provenance: "fs_discovered",
        workspace_id: "w1",
        workspace_title: null,
        workspace_branch: null,
        git_branch: null,
        created_at: null,
        modified_at: null,
        size_bytes: 0,
        title: "Explore",
        first_prompt: null,
        last_prompt: null,
        activity: fleetActivity(),
      },
      turns: [{ user_text: "Map the webapp directory", started_at: null, entries: [] }],
    };
    const fetchMock = vi.fn(async (url: RequestInfo | URL) => {
      expect(String(url)).toContain("/sessions/a1/turns");
      return new Response(JSON.stringify(detail), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    renderWithQuery(
      <WorkPanel
        workspaceId="w1"
        peek={peek()}
        live={WORKING}
        commits={[]}
        sessions={PRIMARY_AND_CHILD}
      />,
    );
    await user.click(screen.getByTestId("work-panel-tab-info"));
    await user.click(screen.getByText(/1 sub-agent/));
    await user.click(screen.getByText("Explore"));

    const transcript = await screen.findByTestId("fleet-child-transcript");
    expect(transcript).toHaveTextContent("Map the webapp directory");
    expect(fetchMock).toHaveBeenCalled();
    vi.unstubAllGlobals();
  });
});

// ─── controls tab (#178) ──────────────────────────────────────────────────────

const CONTROLS = {
  commands: [{ name: "review", scope: "project", detail: "Review the diff" }],
  skills: [{ name: "brainstorming", scope: "user", detail: null }],
  mcp_servers: [{ name: "gitea", scope: "project", detail: null }],
  models: ["sonnet", "opus"],
  current_model: "sonnet",
  permission_mode: null,
};

function mockControls(controls: unknown) {
  const posts: Array<{ url: string; body: unknown }> = [];
  const fetchMock = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const u = String(url);
    if (init?.method === "POST") {
      posts.push({ url: u, body: init.body ? JSON.parse(String(init.body)) : null });
      return new Response(null, { status: 204 });
    }
    if (u.includes("/controls")) {
      return new Response(JSON.stringify(controls), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
    return new Response("{}", { status: 200, headers: { "content-type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { posts };
}

describe("WorkPanel — controls tab (#178)", () => {
  it("enumerates commands, skills, MCP servers, and the current model", async () => {
    mockControls(CONTROLS);
    const user = userEvent.setup();
    renderWithQuery(<WorkPanel workspaceId="w1" peek={peek()} live={WORKING} commits={[]} />);
    await user.click(screen.getByTestId("work-panel-tab-controls"));

    expect(await screen.findByText("/review")).toBeInTheDocument();
    expect(screen.getByText("Review the diff")).toBeInTheDocument();
    expect(screen.getByText("/brainstorming")).toBeInTheDocument();
    expect(screen.getByText("gitea")).toBeInTheDocument();
    // The model switcher shows the current model.
    expect(screen.getByTestId("controls-model-picker")).toHaveTextContent("sonnet");
    vi.unstubAllGlobals();
  });

  it("Run on a command posts /controls/invoke with the control name", async () => {
    const { posts } = mockControls(CONTROLS);
    const user = userEvent.setup();
    renderWithQuery(<WorkPanel workspaceId="w1" peek={peek()} live={WORKING} commits={[]} />);
    await user.click(screen.getByTestId("work-panel-tab-controls"));
    await screen.findByText("/review");

    await user.click(screen.getByRole("button", { name: "Run review" }));

    const invoke = posts.find((p) => p.url.includes("/controls/invoke"));
    expect(invoke).toBeTruthy();
    expect(invoke?.body).toEqual({ name: "review" });
    vi.unstubAllGlobals();
  });

  it("renders a quiet empty state when the agent has no control surface", async () => {
    mockControls({
      commands: [],
      skills: [],
      mcp_servers: [],
      models: [],
      current_model: null,
      permission_mode: null,
    });
    const user = userEvent.setup();
    renderWithQuery(<WorkPanel workspaceId="w1" peek={peek()} live={WORKING} commits={[]} />);
    await user.click(screen.getByTestId("work-panel-tab-controls"));

    expect(await screen.findByText(/No session controls available/)).toBeInTheDocument();
    vi.unstubAllGlobals();
  });
});
