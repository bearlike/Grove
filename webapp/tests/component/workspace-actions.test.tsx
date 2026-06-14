import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { WorkspaceActions } from "@/components/workspace/workspace-actions";
import type { WorkspaceStateView } from "@/lib/grove/types";

function state(overrides: Partial<WorkspaceStateView> = {}): WorkspaceStateView {
  return {
    id: "w1",
    title: "Build it",
    repo_root: "/repo",
    branch: "grove/build-it",
    base_branch: "main",
    worktree_path: "/repo/.worktrees/build-it",
    tmux_session: "grove-build-it",
    agent_name: "claude",
    status: "active",
    created_at: "2026-06-13T10:00:00Z",
    updated_at: "2026-06-13T10:00:00Z",
    branch_provenance: "grove",
    placement: "worktree",
    ...overrides,
  } as WorkspaceStateView;
}

function renderActions(s: WorkspaceStateView) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <WorkspaceActions state={s} />
    </QueryClientProvider>,
  );
}

describe("WorkspaceActions gating", () => {
  it("shows pause + kill for a running workspace", () => {
    renderActions(state({ status: "active" }));
    expect(screen.getByTestId("action-pause")).toBeInTheDocument();
    expect(screen.getByTestId("action-kill")).toBeInTheDocument();
    expect(screen.queryByTestId("action-resume")).toBeNull();
    expect(screen.queryByTestId("action-respawn")).toBeNull();
  });

  it("shows resume for a paused workspace", () => {
    renderActions(state({ status: "paused" }));
    expect(screen.getByTestId("action-resume")).toBeInTheDocument();
    expect(screen.queryByTestId("action-pause")).toBeNull();
  });

  it("shows respawn for an offline workspace", () => {
    renderActions(state({ status: "offline" }));
    expect(screen.getByTestId("action-respawn")).toBeInTheDocument();
  });

  it("shows only kill for orphaned", () => {
    renderActions(state({ status: "orphaned" }));
    expect(screen.getByTestId("action-kill")).toBeInTheDocument();
    expect(screen.queryByTestId("action-pause")).toBeNull();
    expect(screen.queryByTestId("action-respawn")).toBeNull();
  });

  it("hides pause for a root workspace even when active", () => {
    renderActions(state({ status: "active", placement: "root" }));
    expect(screen.queryByTestId("action-pause")).toBeNull();
    expect(screen.getByTestId("action-kill")).toBeInTheDocument();
  });
});
