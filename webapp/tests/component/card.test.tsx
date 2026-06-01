import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ThemeProvider } from "next-themes";
import { WorkspaceCard } from "@/components/workspace/card";
import { WorkspaceCardModel } from "@/lib/grove/workspace-card";
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
    ...over,
  } as WorkspaceStateView;
}

function r(node: React.ReactNode) {
  return render(<ThemeProvider attribute="class" defaultTheme="dark">{node}</ThemeProvider>);
}

describe("WorkspaceCard", () => {
  it("renders title, branch, agent, and status badge", () => {
    const model = WorkspaceCardModel.fromState(ws());
    r(<WorkspaceCard model={model} peek={undefined} />);
    expect(screen.getByText("feat work")).toBeInTheDocument();
    expect(screen.getByText("feat/x")).toBeInTheDocument();
    expect(screen.getByText("claude")).toBeInTheDocument();
    expect(screen.getByTestId("status-badge")).toBeInTheDocument();
  });

  it("links to /w/{id}", () => {
    const model = WorkspaceCardModel.fromState(ws({ id: "abc" }));
    r(<WorkspaceCard model={model} peek={undefined} />);
    const link = screen.getByRole("link");
    expect(link.getAttribute("href")).toBe("/w/abc");
  });

  it("has data-status matching workspace status", () => {
    const model = WorkspaceCardModel.fromState(ws({ status: "orphaned" }));
    r(<WorkspaceCard model={model} peek={undefined} />);
    expect(screen.getByTestId("workspace-card").dataset.status).toBe("orphaned");
  });
});
