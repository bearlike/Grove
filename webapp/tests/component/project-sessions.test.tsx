import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ProjectSessions } from "@/components/workspace/project-sessions";
import type { SessionSummaryView } from "@/lib/grove/types";

const REPO = "/repos/Grove";

function session(over: Partial<SessionSummaryView> = {}): SessionSummaryView {
  return {
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
    title: "wire the sessions section",
    first_prompt: "build the sessions section",
    last_prompt: "now the tests",
    activity: {
      state: "working",
      title: "ai: wiring the section",
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
    ...over,
  };
}

/** A hand-staged session: no owning workspace, only a raw git branch. */
function handSession(over: Partial<SessionSummaryView> = {}): SessionSummaryView {
  return session({
    session_id: "s-hand",
    provenance: "fs_discovered",
    workspace_id: null,
    workspace_title: null,
    workspace_branch: null,
    git_branch: "main",
    title: null,
    first_prompt: "poke around the repo",
    ...over,
  });
}

const TURNS_DETAIL = {
  session: session(),
  turns: [
    {
      user_text: "build the sessions section",
      started_at: "2026-06-10T10:00:00Z",
      entries: [{ role: "assistant", text: "On it." }],
    },
  ],
};

// Real code paths through useProjectSessions/useSessionTurns — stub only the
// fetch boundary, dispatching on URL so the sessions list and a row's turns
// drill-down can be served from the same stub.
function stubFetch(sessions: SessionSummaryView[]) {
  const mock = vi.fn().mockImplementation((url: string) => {
    const body = url.includes("/turns") ? TURNS_DETAIL : sessions;
    return Promise.resolve(
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
  });
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

describe("ProjectSessions", () => {
  it("is collapsed by default, self-describing, and fetches nothing", () => {
    const mock = stubFetch([session()]);
    r(<ProjectSessions repoRoot={REPO} />);

    const toggle = screen.getByTestId("project-sessions-toggle");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    // The collapsed header explains the feature without expansion.
    expect(toggle).toHaveTextContent("Sessions");
    expect(toggle).toHaveTextContent("Grove-managed and hand-started");
    expect(screen.queryByTestId("session-row")).toBeNull();
    expect(mock).not.toHaveBeenCalled();
  });

  it("expanding fetches the project's sessions and renders rows", async () => {
    const mock = stubFetch([session(), session({ session_id: "s2", title: null })]);
    const user = userEvent.setup();
    r(<ProjectSessions repoRoot={REPO} />);

    await user.click(screen.getByTestId("project-sessions-toggle"));
    const rows = await screen.findAllByTestId("session-row");
    expect(mock).toHaveBeenCalledWith(
      `/api/grove/sessions?repo=${encodeURIComponent(REPO)}`,
      expect.objectContaining({ method: "GET" }),
    );
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveAttribute("data-session-id", "s1");
    expect(rows[0]).toHaveTextContent("wire the sessions section");
    expect(rows[0]).toHaveTextContent("working");
    expect(rows[0]).toHaveTextContent("claude-opus-4-8");
    // No title → first_prompt stands in.
    expect(rows[1]).toHaveTextContent("build the sessions section");
  });

  it("grove rows carry workspace attribution linking to the detail page", async () => {
    stubFetch([session()]);
    const user = userEvent.setup();
    r(<ProjectSessions repoRoot={REPO} />);
    await user.click(screen.getByTestId("project-sessions-toggle"));

    const link = await screen.findByTestId("session-workspace-link");
    expect(link).toHaveAttribute("href", "/w/w1");
    expect(link).toHaveTextContent("feat depth");
    expect(link).toHaveTextContent("feat/depth");
  });

  it("hand-staged rows show hand-started provenance + git branch, no workspace link", async () => {
    stubFetch([session(), handSession()]);
    const user = userEvent.setup();
    r(<ProjectSessions repoRoot={REPO} />);
    await user.click(screen.getByTestId("project-sessions-toggle"));

    const rows = await screen.findAllByTestId("session-row");
    const tags = screen.getAllByTestId("session-provenance");
    expect(tags[0]).toHaveTextContent("grove");
    expect(tags[1]).toHaveTextContent("hand-started");
    expect(within(rows[1]).queryByTestId("session-workspace-link")).toBeNull();
    expect(rows[1]).toHaveTextContent("main");
  });

  it("drill-down: grove rows expand into the turns view, hand-staged rows offer none", async () => {
    stubFetch([session(), handSession()]);
    const user = userEvent.setup();
    r(<ProjectSessions repoRoot={REPO} />);
    await user.click(screen.getByTestId("project-sessions-toggle"));

    const rows = await screen.findAllByTestId("session-row");
    // The hand-staged row has no expand affordance at all (no dead chevron).
    expect(within(rows[1]).queryByRole("button")).toBeNull();

    const toggle = within(rows[0]).getByRole("button");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("turns-view")).toBeNull();
    await user.click(toggle);
    expect(await screen.findByTestId("turns-view")).toBeInTheDocument();
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    await user.click(toggle);
    expect(screen.queryByTestId("turns-view")).toBeNull();
  });

  it("degrades to a quiet empty line when the project has no sessions", async () => {
    stubFetch([]);
    const user = userEvent.setup();
    r(<ProjectSessions repoRoot={REPO} />);
    await user.click(screen.getByTestId("project-sessions-toggle"));

    expect(
      await screen.findByText("no recorded sessions in this project"),
    ).toBeInTheDocument();
  });

  it("shows the muted error line when the fetch fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("boom")));
    const user = userEvent.setup();
    r(<ProjectSessions repoRoot={REPO} />);
    await user.click(screen.getByTestId("project-sessions-toggle"));

    expect(await screen.findByText("couldn't load sessions")).toBeInTheDocument();
  });
});
