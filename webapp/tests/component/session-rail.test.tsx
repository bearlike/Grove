import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SessionRail } from "@/components/layout/session-rail";
import type { DashboardFacets } from "@/lib/grove/dashboard-filter";
import type {
  AgentActivityView,
  DashboardSnapshotView,
  SessionSummaryView,
  WorkspaceActivityView,
  WorkspaceStateView,
} from "@/lib/grove/types";

// The rail reads `useProjectSessionsAll` (seeded here) and renders a
// FLAT, cross-project list ordered `modified_at` DESC — no project sections, no
// date groups, no attention pin. Each row carries a provenance meta line
// (project · branch · ±change). Rows are "mapped" when their workspace is present
// in the live snapshot; unmapped (metadata-only) rows are HIDDEN by default and
// summarized by a hidden-note. `usePathname`/`useSearchParams` are overridden
// (SessionRail reads them to mark the active row).

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof import("next/navigation")>();
  return {
    ...actual,
    usePathname: () => "/",
    useSearchParams: () => new URLSearchParams(),
  };
});

function activity(over: Partial<AgentActivityView> = {}): AgentActivityView {
  return {
    state: "idle",
    title: null,
    current_task: null,
    human_turns: 0,
    assistant_replies: 0,
    replies_per_turn: [],
    tool_calls: 0,
    active_subagents: 0,
    model: "claude-opus-4-8",
    tokens_in: 0,
    tokens_out: 0,
    last_event_at: null,
    needs_attention: false,
    error_detail: null,
    questions: [],
    ...over,
  };
}

function session(over: Partial<SessionSummaryView> & { session_id: string }): SessionSummaryView {
  return {
    adapter_kind: "claude_code",
    provenance: "grove_launched",
    primary: true,
    workspace_id: "w1",
    workspace_title: "feat",
    workspace_branch: "dev/feat",
    git_branch: "dev/feat",
    created_at: "2026-05-01T10:00:00Z",
    modified_at: "2026-05-01T10:00:00Z",
    size_bytes: 100,
    live: false,
    title: "a session",
    first_prompt: "do a thing",
    last_prompt: "wrap up",
    activity: activity(),
    ...over,
  };
}

const FACETS: DashboardFacets = {
  projects: [{ repo_root: "/repos/Grove", repo_name: "Grove", count: 3 }],
  states: [],
  attention: 0,
  total: 3,
};

/** A live workspace-activity view — its presence in the snapshot makes a row
 *  "mapped", and its diff_added/diff_removed feed the row's ±change slot. */
function wsActivity(
  id: string,
  over: Partial<WorkspaceActivityView> = {},
): WorkspaceActivityView {
  const state = {
    id,
    title: id,
    repo_root: "/repos/Grove",
    branch: "dev/feat",
    base_branch: "main",
    worktree_path: `/repos/Grove/.worktrees/${id}`,
    tmux_session: `grove-${id}`,
    agent_name: "claude",
    status: "running",
    created_at: "2026-05-01T10:00:00Z",
    updated_at: "2026-05-01T10:00:00Z",
    paused_at: null,
    runtime: id === "w2" ? "container" : "host",
  } as unknown as WorkspaceStateView;
  return {
    state,
    sessions: [],
    base_ahead: 0,
    base_behind: 0,
    diff_added: 0,
    diff_removed: 0,
    dirty_files: 0,
    pane_target: null,
    needs_attention: false,
    recent_commits: [],
    observed_at: "2026-05-01T10:00:00Z",
    ...over,
  };
}

/** A snapshot whose workspaces w1 (30/−4 diff) + w2 (no diff) map the two
 *  attributed rows; the hand-staged row has no workspace and stays unmapped. */
function mappedSnapshot(): DashboardSnapshotView {
  return {
    projects: [
      {
        repo_root: "/repos/Grove",
        repo_name: "Grove",
        cwd: "/repos/Grove",
        workspaces: [
          wsActivity("w1", { diff_added: 30, diff_removed: 4 }),
          wsActivity("w2"),
        ],
      },
    ],
    generated_at: "2026-07-06T00:00:00Z",
    total_workspaces: 2,
    needs_attention: 0,
  } as unknown as DashboardSnapshotView;
}

const ROWS: SessionSummaryView[] = [
  session({
    session_id: "s-live",
    workspace_id: "w1",
    title: "fix auth",
    git_branch: "dev/fix-auth",
    modified_at: "2026-05-02T10:00:00Z", // newest → sorts first
    activity: activity({ state: "working" }),
  }),
  session({
    session_id: "s-att",
    workspace_id: "w2",
    title: "add cache",
    git_branch: "dev/add-cache",
    modified_at: "2026-05-01T10:00:00Z",
    activity: activity({ state: "waiting", needs_attention: true }),
  }),
  // Hand-staged: null workspace id → metadata-only, unmapped, hidden by default.
  session({
    session_id: "s-hand",
    workspace_id: null,
    workspace_title: null,
    workspace_branch: null,
    git_branch: "main",
    title: null,
    first_prompt: "poke around",
  }),
];

function renderRail(
  props: Partial<React.ComponentProps<typeof SessionRail>> = {},
  rows: SessionSummaryView[] = ROWS,
) {
  const qc = new QueryClient({
    defaultOptions: { queries: { staleTime: Infinity, retry: false } },
  });
  qc.setQueryData(["project-sessions", "/repos/Grove"], rows);
  return render(
    <QueryClientProvider client={qc}>
      <SessionRail snapshot={mappedSnapshot()} facets={FACETS} query="" {...props} />
    </QueryClientProvider>,
  );
}

describe("SessionRail", () => {
  it("renders a flat list ordered by modified_at DESC — no groups, no attention pin", () => {
    renderRail();
    // No section/group/attention-pin chrome survives.
    expect(screen.queryByTestId("session-rail-attention")).toBeNull();
    expect(screen.queryByTestId("session-rail-project")).toBeNull();
    expect(screen.queryByTestId("session-rail-group")).toBeNull();
    // Mapped rows only, newest first (s-live modified after s-att).
    const ids = screen.getAllByTestId("session-rail-row").map((r) => r.getAttribute("data-session-id"));
    expect(ids).toEqual(["s-live", "s-att"]);
  });

  it("carries a provenance meta line: project · branch · ±change from the live workspace", () => {
    renderRail();
    const live = screen.getByText("fix auth").closest('[data-testid="session-rail-row"]')!;
    expect(live).toHaveTextContent("Grove");
    expect(live).toHaveTextContent("dev/fix-auth");
    // Real ±line counts from w1's diff_added/diff_removed (30 / 4).
    const changes = within(live as HTMLElement).getByTestId("session-rail-changes");
    expect(changes).toHaveTextContent("+30");
    expect(within(changes).getByText("+30")).toHaveStyle({ color: "var(--ref-add)" });
    expect(changes.textContent).toContain("4");

    // w2 has zero change data → the slot is blank (no noise)…
    const att = screen.getByText("add cache").closest('[data-testid="session-rail-row"]')!;
    expect(within(att as HTMLElement).queryByTestId("session-rail-changes")).toBeNull();
    expect(att).toHaveAttribute("data-attention", "true");
    // …and the row's text ends CLEANLY at the branch — no dangling trailing
    // middot (the ⋯ menu contributes no text, so the row's last text IS the
    // meta line's tail). Regression pin: MetaRow drops falsy children, but an
    // element whose component returns null is truthy, so the ChangeStat mount
    // must be gated at JSX level.
    expect((att.textContent ?? "").trim()).not.toMatch(/·$/);
  });

  it("carries the task phase as a quiet meta sigil, absent when unreported", () => {
    const snap = mappedSnapshot();
    snap.projects[0].workspaces[0].phase = {
      phase: "verifying",
      note: null,
      updated_at: "2026-05-02T10:00:00Z",
      index: 3,
      total: 6,
    };
    renderRail({ snapshot: snap });
    const live = screen.getByText("fix auth").closest('[data-testid="session-rail-row"]')!;
    const badge = within(live as HTMLElement).getByTestId("phase-badge");
    expect(badge).toHaveAttribute("data-phase", "verifying");
    expect(badge).toHaveTextContent("4/6");

    // w2 reports no phase → no slot, and (the MetaRow gate) no dangling middot.
    const att = screen.getByText("add cache").closest('[data-testid="session-rail-row"]')!;
    expect(within(att as HTMLElement).queryByTestId("phase-badge")).toBeNull();
    expect((att.textContent ?? "").trim()).not.toMatch(/·$/);
  });

  it("marks the runtime on every MAPPED row, and stays honest on an unmapped one", () => {
    // Both runtimes are marked — the isolation axis has no silent state. An
    // unmapped (history-only) row has no live workspace, so its runtime is
    // genuinely unknown and the slot is absent rather than defaulted to host.
    renderRail({ snapshot: mappedSnapshot(), showUnmapped: true });
    const live = screen.getByText("fix auth").closest('[data-testid="session-rail-row"]')!;
    const att = screen.getByText("add cache").closest('[data-testid="session-rail-row"]')!;
    expect(within(live as HTMLElement).getByTestId("runtime-mark").dataset.runtime).toBe("host");
    expect(within(att as HTMLElement).getByTestId("runtime-mark").dataset.runtime).toBe(
      "container",
    );

    const hand = screen.getByText("poke around").closest('[data-testid="session-rail-row"]')!;
    expect(within(hand as HTMLElement).queryByTestId("runtime-mark")).toBeNull();
  });

  it("shows a visible created-ago on every row and a dotted-underline project cue", () => {
    renderRail();
    const live = screen.getByText("fix auth").closest('[data-testid="session-rail-row"]')!;
    // Created-ago is visible (not tooltip-only) — every row carries one.
    expect(within(live as HTMLElement).getByTestId("session-rail-age")).toBeInTheDocument();
    expect(screen.getAllByTestId("session-rail-age")).toHaveLength(2);
    // The project name wears the subtle dotted underline — the project/branch cue.
    const project = within(live as HTMLElement).getByTestId("session-rail-project-name");
    expect(project).toHaveTextContent("Grove");
    expect(project.className).toContain("decoration-dotted");
  });

  it("hides unmapped rows by default and summarizes them with a hidden-note that reveals them", async () => {
    const onShowUnmapped = vi.fn();
    const { rerender } = renderRail({ onShowUnmapped });
    // The hand-staged row is not rendered by default.
    expect(screen.queryByText("poke around")).toBeNull();
    const note = screen.getByTestId("session-rail-hidden-note");
    expect(note).toHaveTextContent("1 unmapped session hidden");
    await userEvent.click(note);
    expect(onShowUnmapped).toHaveBeenCalledOnce();

    // With showUnmapped on, the note is gone and the inert history-only row shows.
    const qc = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, retry: false } } });
    qc.setQueryData(["project-sessions", "/repos/Grove"], ROWS);
    rerender(
      <QueryClientProvider client={qc}>
        <SessionRail snapshot={mappedSnapshot()} facets={FACETS} query="" showUnmapped />
      </QueryClientProvider>,
    );
    expect(screen.queryByTestId("session-rail-hidden-note")).toBeNull();
    const hand = screen.getByText("poke around").closest('[data-testid="session-rail-row"]')!;
    expect(hand).toHaveAttribute("data-navigable", "false");
    expect(hand).toHaveAttribute("data-mapped", "false");
    expect(within(hand as HTMLElement).queryByRole("link")).toBeNull();
  });

  it("makes an attributed row navigate to /w/{id}?s={sid} with a hover lifecycle menu", () => {
    renderRail();
    const live = screen.getByRole("link", { name: /fix auth/ });
    expect(live).toHaveAttribute("href", "/w/w1?s=s-live");
    const row = live.closest('[data-testid="session-rail-row"]')!;
    expect(within(row as HTMLElement).getByTestId("session-rail-row-menu")).toBeInTheDocument();
  });

  it("filters the list by the search query (title / prompt / branch)", () => {
    renderRail({ query: "cache" });
    const rows = screen.getAllByTestId("session-rail-row");
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveAttribute("data-session-id", "s-att");
  });

  it("hides a whole project when its repo_root is in hiddenProjects", () => {
    renderRail({ hiddenProjects: ["/repos/Grove"] });
    expect(screen.queryByTestId("session-rail-row")).toBeNull();
  });

  it("acknowledges active filters in the empty state and offers a clear action", async () => {
    const onClearFilters = vi.fn();
    renderRail({ hiddenProjects: ["/repos/Grove"], onClearFilters });
    const empty = screen.getByTestId("session-rail-empty");
    expect(empty).toHaveTextContent(/active filters/i);
    await userEvent.click(screen.getByTestId("session-rail-empty-clear"));
    expect(onClearFilters).toHaveBeenCalledOnce();
  });

  it("shows the genuinely-empty copy (no clear) when there are no sessions at all", () => {
    renderRail({}, []);
    const empty = screen.getByTestId("session-rail-empty");
    expect(empty).toHaveTextContent(/no sessions yet/i);
    expect(screen.queryByTestId("session-rail-empty-clear")).toBeNull();
  });
});
