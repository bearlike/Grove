import { describe, it, expect } from "vitest";
import { RepoFacet } from "@/lib/grove/repo-facet";
import type { WorkspaceStateView } from "@/lib/grove/types";

function ws(over: Partial<WorkspaceStateView>): WorkspaceStateView {
  return {
    id: "w-default",
    title: "default",
    repo_root: "/repos/Grove",
    branch: "main",
    base_branch: "main",
    worktree_path: "/repos/Grove/.worktrees/x",
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

describe("RepoFacet.groupByRepo", () => {
  it("partitions workspaces by repo_root", () => {
    const views = [
      ws({ id: "g1", repo_root: "/repos/Grove" }),
      ws({ id: "g2", repo_root: "/repos/Grove" }),
      ws({ id: "o1", repo_root: "/repos/other" }),
    ];
    const facets = RepoFacet.groupByRepo(views);
    expect(facets).toHaveLength(2);
    const grove = facets.find((f) => f.repoName === "Grove")!;
    expect(grove.workspaces).toHaveLength(2);
  });

  it("derives repoName from basename of repo_root (handles trailing slash)", () => {
    const facets = RepoFacet.groupByRepo([
      ws({ repo_root: "/repos/Grove/" }),
      ws({ repo_root: "/var/projects/website" }),
    ]);
    expect(facets.map((f) => f.repoName).sort()).toEqual(["Grove", "website"]);
  });

  it("sorts facets by repoName ascending", () => {
    const facets = RepoFacet.groupByRepo([
      ws({ repo_root: "/x/zulu" }),
      ws({ repo_root: "/x/alpha" }),
      ws({ repo_root: "/x/mike" }),
    ]);
    expect(facets.map((f) => f.repoName)).toEqual(["alpha", "mike", "zulu"]);
  });

  it("sorts workspaces inside a facet by updated_at desc", () => {
    const facet = RepoFacet.groupByRepo([
      ws({ id: "old", updated_at: "2026-01-01T00:00:00Z" }),
      ws({ id: "new", updated_at: "2026-05-01T00:00:00Z" }),
    ])[0];
    expect(facet.workspaces.map((w) => w.id)).toEqual(["new", "old"]);
  });

  it("counts active / idle / offline / attention", () => {
    const facet = RepoFacet.groupByRepo([
      ws({ status: "active" }),
      ws({ status: "active" }),
      ws({ status: "idle" }),
      ws({ status: "offline" }),
      ws({ status: "orphaned" }),
      ws({ status: "error" }),
    ])[0];
    expect(facet.total).toBe(6);
    expect(facet.activeCount).toBe(2);
    expect(facet.idleCount).toBe(1);
    expect(facet.offlineCount).toBe(1);
    expect(facet.attentionCount).toBe(2);
  });
});
