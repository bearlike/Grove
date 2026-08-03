import { describe, it, expect } from "vitest";
import { useState, type ReactNode } from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ContextBar } from "@/components/workspace/context-bar";
import { AgentLiveStatus } from "@/lib/grove/agent-activity";
import type {
  AgentActivityView,
  PhaseView,
  TicketRef,
  WorkspacePeekView,
  WorkspaceStateView,
} from "@/lib/grove/types";

// The consolidated session header: a state-led title trigger that opens
// ONE popover. These pin the preserved seams:
// `identity-trigger` opens `branch-summary`; inside live `stat-trio`,
// `placement-badge`, `branch-delta-dot`, and the folded-in lifecycle verbs
// (`action-pause`/`action-resume`/`action-kill`). The full `commit-list` is NOT
// duplicated here (it lives in the work panel's Diff tab). The kill →
// KillConfirmDialog flow is e2e-only (Radix focus-trap OOMs jsdom) — here we
// only assert the kill trigger renders, never click it open.

function Providers({ children }: { children: ReactNode }) {
  const [qc] = useState(
    () => new QueryClient({ defaultOptions: { queries: { retry: false } } }),
  );
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

function stateView(overrides: Partial<WorkspaceStateView> = {}): WorkspaceStateView {
  return {
    id: "w1",
    title: "Build the thing",
    description: "the full task description",
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
    runtime: "host",
    ...overrides,
  } as WorkspaceStateView;
}

function peek(overrides: Partial<WorkspacePeekView> = {}): WorkspacePeekView {
  return {
    state: stateView(),
    base_ahead: 3,
    base_behind: 0,
    diff_added: 10,
    diff_removed: 4,
    dirty_files: 2,
    recent_commits: [],
    agent_snapshot: null,
    snapshot_taken_at: null,
    ...overrides,
  } as WorkspacePeekView;
}

const PHASE: PhaseView = {
  phase: "verifying",
  note: "running the gates",
  updated_at: "2026-07-31T10:00:00Z",
  index: 3,
  total: 6,
};

const ISSUE: TicketRef = {
  provider: "gitea",
  id: "330",
  kind: "issue",
  title: "webapp under-displays the axes",
  url: "https://git.example/bearlike/Grove/issues/330",
  status: "open",
  assignee: null,
  ambiguous: false,
} as TicketRef;

const WORKING = AgentLiveStatus.of({ state: "working", model: "sonnet-4-5" } as AgentActivityView);
const NO_SESSION = AgentLiveStatus.of(null);

function r(node: React.ReactNode) {
  return render(node, { wrapper: Providers });
}

describe("ContextBar", () => {
  it("leads with the agent state glyph + title when a session exists", () => {
    r(<ContextBar peek={peek()} live={WORKING} />);
    expect(screen.getByTestId("identity-trigger")).toBeInTheDocument();
    expect(screen.getByTestId("state-mark")).toHaveAttribute("data-state", "working");
    // The title renders twice by design: the sr-only <h1> (document heading —
    // a heading can't live inside the trigger button) + the visible trigger span.
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Build the thing");
    expect(within(screen.getByTestId("identity-trigger")).getByText("Build the thing")).toBeInTheDocument();
    expect(screen.queryByTestId("status-badge")).toBeNull();
  });

  it("falls back to the lifecycle StatusBadge when there is no session", () => {
    r(<ContextBar peek={peek()} live={NO_SESSION} />);
    expect(screen.getByTestId("status-badge")).toHaveAttribute("data-status", "active");
  });

  it("announces the state word via an sr-only aria-live region (never task text)", () => {
    r(<ContextBar peek={peek()} live={WORKING} />);
    const live = screen.getByTestId("session-state-live");
    expect(live).toHaveTextContent("working");
    expect(live).toHaveAttribute("aria-live", "polite");
    // The description/prompt text must NEVER ride the announced region.
    expect(live).not.toHaveTextContent("the full task description");
  });

  it("rides the amber delta dot only when the branch has a delta", () => {
    const { rerender } = r(<ContextBar peek={peek({ base_ahead: 3 })} live={WORKING} />);
    expect(screen.getByTestId("branch-delta-dot")).toBeInTheDocument();

    rerender(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <ContextBar peek={peek({ base_ahead: 0, base_behind: 0, dirty_files: 0 })} live={WORKING} />
      </QueryClientProvider>,
    );
    expect(screen.queryByTestId("branch-delta-dot")).toBeNull();
  });

  it("opens a popover carrying branch identity, the stat trio, and placement — but not the commit list", async () => {
    const user = userEvent.setup();
    r(<ContextBar peek={peek({ state: stateView({ placement: "root" }) })} live={WORKING} />);
    await user.click(screen.getByTestId("identity-trigger"));
    const summary = await screen.findByTestId("branch-summary");
    expect(summary).toBeInTheDocument();
    // Identity section — branch → base · agent/model, the deleted strip's data.
    expect(summary).toHaveTextContent("grove/build-the-thing");
    expect(summary).toHaveTextContent("main");
    expect(summary).toHaveTextContent("claude/sonnet-4-5");
    // Changes section — the stat trio; placement shows for a root workspace.
    expect(screen.getByTestId("stat-trio")).toBeInTheDocument();
    expect(screen.getByTestId("placement-badge")).toBeInTheDocument();
    // The full commit list is NOT duplicated here — it lives in the Diff tab.
    expect(screen.queryByTestId("commit-list")).toBeNull();
  });

  it("names the runtime in the popover for BOTH runtimes, host included", async () => {
    // The session surface must not lose an axis the wall shows — and unlike
    // placement, the host case is never silent here (see RuntimeBadge).
    const user = userEvent.setup();
    const { rerender } = r(<ContextBar peek={peek()} live={WORKING} />);
    await user.click(screen.getByTestId("identity-trigger"));
    expect((await screen.findByTestId("runtime-badge")).dataset.runtime).toBe("host");

    rerender(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <ContextBar
          peek={peek({ state: stateView({ runtime: "container" }) })}
          live={WORKING}
        />
      </QueryClientProvider>,
    );
    await user.click(screen.getByTestId("identity-trigger"));
    expect((await screen.findByTestId("runtime-badge")).dataset.runtime).toBe("container");
  });

  // ── the third axis + linked refs ──────────────────────────────────────────
  // The phase axis must render here too, not only on the overview grid card,
  // so opening a workspace never loses an axis you could see from the wall.

  it("wears the task phase on the trigger, so opening a workspace keeps the axis", () => {
    r(<ContextBar peek={peek()} live={WORKING} phase={PHASE} />);
    const badge = within(screen.getByTestId("identity-trigger")).getByTestId("phase-badge");
    expect(badge).toHaveAttribute("data-phase", "verifying");
    expect(badge).toHaveTextContent("4/6");
  });

  it("renders no phase chrome at all when the agent reports no phase", () => {
    r(<ContextBar peek={peek()} live={WORKING} />);
    expect(screen.queryByTestId("phase-badge")).toBeNull();
  });

  it("opens the popover onto a Task section carrying the meter, name and note", async () => {
    const user = userEvent.setup();
    r(<ContextBar peek={peek()} live={WORKING} phase={PHASE} />);
    await user.click(screen.getByTestId("identity-trigger"));
    const summary = await screen.findByTestId("branch-summary");
    expect(within(summary).getByTestId("phase-meter")).toHaveTextContent("verifying");
    expect(within(summary).getByTestId("phase-note")).toHaveTextContent("running the gates");
    expect(within(summary).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "4");
  });

  it("links the issue in the popover even when no PR exists yet", async () => {
    const user = userEvent.setup();
    r(<ContextBar peek={peek({ state: stateView({ ticket_refs: [ISSUE] }) })} live={WORKING} />);
    await user.click(screen.getByTestId("identity-trigger"));
    const summary = await screen.findByTestId("branch-summary");
    expect(within(summary).getByTestId("ticket-linkage")).toBeInTheDocument();
    expect(within(summary).getByRole("link", { name: /issue #330/i })).toHaveAttribute(
      "href",
      "https://git.example/bearlike/Grove/issues/330",
    );
  });

  it("hides the Task and Links sections entirely when there is nothing to show", async () => {
    const user = userEvent.setup();
    r(<ContextBar peek={peek()} live={WORKING} />);
    await user.click(screen.getByTestId("identity-trigger"));
    const summary = await screen.findByTestId("branch-summary");
    expect(summary).not.toHaveTextContent("Task");
    expect(summary).not.toHaveTextContent("Links");
    expect(within(summary).queryByTestId("phase-meter")).toBeNull();
    expect(within(summary).queryByTestId("ticket-linkage")).toBeNull();
  });

  it("hosts the reversible pause verb in the popover for an active workspace", async () => {
    const user = userEvent.setup();
    r(<ContextBar peek={peek({ state: stateView({ status: "active" }) })} live={WORKING} />);
    await user.click(screen.getByTestId("identity-trigger"));
    expect(await screen.findByTestId("action-pause")).toBeInTheDocument();
    expect(screen.queryByTestId("action-resume")).toBeNull();
  });

  it("swaps to the resume verb in the popover for a paused workspace", async () => {
    const user = userEvent.setup();
    r(<ContextBar peek={peek({ state: stateView({ status: "paused" }) })} live={NO_SESSION} />);
    await user.click(screen.getByTestId("identity-trigger"));
    expect(await screen.findByTestId("action-resume")).toBeInTheDocument();
    expect(screen.queryByTestId("action-pause")).toBeNull();
  });

  it("hosts the relocated kill control in the popover danger zone", async () => {
    const user = userEvent.setup();
    r(<ContextBar peek={peek()} live={WORKING} />);
    await user.click(screen.getByTestId("identity-trigger"));
    // Kill folded in from the deleted LifecycleMenu; assert the trigger renders
    // (the confirm-dialog open flow is exercised by the e2e spec).
    expect(await screen.findByTestId("action-kill")).toBeInTheDocument();
  });

  it("renders the placement badge for a root workspace via the popover", async () => {
    const user = userEvent.setup();
    r(<ContextBar peek={peek({ state: stateView({ placement: "root" }) })} live={WORKING} />);
    await user.click(screen.getByTestId("identity-trigger"));
    expect(await screen.findByTestId("placement-badge")).toBeInTheDocument();
  });
});
