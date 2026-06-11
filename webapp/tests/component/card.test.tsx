import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ThemeProvider } from "next-themes";
import { WorkspaceCard } from "@/components/workspace/card";
import { WorkspaceCardModel } from "@/lib/grove/workspace-card";
import { workspace } from "@/tests/_helpers/activity-fixtures";
import type { WorkspaceStateView } from "@/lib/grove/types";

function ws(over: Partial<WorkspaceStateView> = {}): WorkspaceStateView {
  return {
    id: "w1",
    title: "feat work",
    repo_root: "/repos/Grove",
    branch: "feat/x",
    base_branch: "main",
    worktree_path: "/x",
    tmux_session: "grove-x",
    agent_name: "claude",
    status: "active",
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-01T00:00:00Z",
    paused_at: null,
    error_detail: null,
    description: null,
    init_status: null,
    init_duration_ms: null,
    branch_provenance: "grove",
    placement: "worktree",
    ...over,
  } as WorkspaceStateView;
}

/** Activity-shaped model: the home grid's data source since the SSE switch. */
function fromActivity(over: Partial<WorkspaceStateView> = {}, stats?: {
  base_ahead?: number;
  base_behind?: number;
  dirty_files?: number;
}) {
  const a = workspace("w1", "working");
  return WorkspaceCardModel.fromActivity({
    ...a,
    ...stats,
    state: ws(over),
  });
}

function r(node: React.ReactNode) {
  return render(<ThemeProvider attribute="class" defaultTheme="dark">{node}</ThemeProvider>);
}

describe("WorkspaceCard", () => {
  it("renders title, branch, agent, and status badge", () => {
    r(<WorkspaceCard model={fromActivity()} />);
    expect(screen.getByText("feat work")).toBeInTheDocument();
    expect(screen.getByText("feat/x")).toBeInTheDocument();
    expect(screen.getByText("claude")).toBeInTheDocument();
    expect(screen.getByTestId("status-badge")).toBeInTheDocument();
  });

  it("links to /w/{id}", () => {
    r(<WorkspaceCard model={fromActivity({ id: "abc" })} />);
    const link = screen.getByRole("link");
    expect(link.getAttribute("href")).toBe("/w/abc");
  });

  it("has data-status matching workspace status", () => {
    r(<WorkspaceCard model={fromActivity({ status: "orphaned" })} />);
    expect(screen.getByTestId("workspace-card").dataset.status).toBe("orphaned");
  });

  it("renders the stat trio from activity-view counts (incl. dirty_files)", () => {
    r(
      <WorkspaceCard
        model={fromActivity({}, { base_ahead: 4, base_behind: 1, dirty_files: 2 })}
      />,
    );
    expect(screen.getByTestId("stat-trio")).toBeInTheDocument();
    expect(screen.getByTestId("stat-ahead")).toHaveTextContent("4");
    expect(screen.getByTestId("stat-behind")).toHaveTextContent("1");
    expect(screen.getByTestId("stat-dirty")).toHaveTextContent("2");
  });

  it("renders the stat placeholder when the model has no stats", () => {
    r(<WorkspaceCard model={WorkspaceCardModel.fromState(ws())} />);
    expect(screen.queryByTestId("stat-trio")).toBeNull();
  });

  it("shows a root placement badge for root workspaces", () => {
    r(<WorkspaceCard model={fromActivity({ placement: "root" })} />);
    const badge = screen.getByTestId("placement-badge");
    expect(badge).toBeInTheDocument();
    expect(badge.dataset.placement).toBe("root");
    expect(badge).toHaveTextContent("root");
  });

  it("renders no placement badge for worktree workspaces", () => {
    r(<WorkspaceCard model={fromActivity({ placement: "worktree" })} />);
    expect(screen.queryByTestId("placement-badge")).toBeNull();
  });
});
