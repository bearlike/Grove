import type { WorkspaceActivityView, WorkspaceStateView } from "./types";

/**
 * Atomic class: one facet groups all workspaces under a single repo_root.
 * Construction is via the static factory; instances are immutable.
 */
export class RepoFacet {
  readonly repoRoot: string;
  readonly repoName: string;
  readonly workspaces: ReadonlyArray<WorkspaceStateView>;

  private constructor(repoRoot: string, workspaces: ReadonlyArray<WorkspaceStateView>) {
    this.repoRoot = repoRoot;
    this.repoName = RepoFacet._basename(repoRoot);
    this.workspaces = workspaces;
  }

  static groupByRepo(views: WorkspaceStateView[]): RepoFacet[] {
    const groups = new Map<string, WorkspaceStateView[]>();
    for (const v of views) {
      const key = v.repo_root.replace(/\/+$/, "");
      const arr = groups.get(key) ?? [];
      arr.push(v);
      groups.set(key, arr);
    }
    const facets: RepoFacet[] = [];
    for (const [root, members] of groups) {
      members.sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1));
      facets.push(new RepoFacet(root, members));
    }
    facets.sort((a, b) => a.repoName.localeCompare(b.repoName));
    return facets;
  }

  /**
   * Adapter for activity-stream consumers: group on the embedded workspace
   * state so grouping/sorting/counting stay defined once. Callers keep the
   * full activity views and re-pair members by id.
   */
  static groupActivityByRepo(views: WorkspaceActivityView[]): RepoFacet[] {
    return RepoFacet.groupByRepo(views.map((v) => v.state));
  }

  static _basename(p: string): string {
    const trimmed = p.replace(/\/+$/, "");
    const idx = trimmed.lastIndexOf("/");
    return idx === -1 ? trimmed : trimmed.slice(idx + 1);
  }

  get total(): number {
    return this.workspaces.length;
  }

  get activeCount(): number {
    return this.workspaces.filter((w) => w.status === "active" || w.status === "running").length;
  }

  get idleCount(): number {
    return this.workspaces.filter((w) => w.status === "idle").length;
  }

  get offlineCount(): number {
    return this.workspaces.filter((w) => w.status === "offline" || w.status === "paused").length;
  }

  get attentionCount(): number {
    return this.workspaces.filter((w) => w.status === "orphaned" || w.status === "error").length;
  }
}
