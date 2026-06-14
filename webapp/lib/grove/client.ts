import type {
  AgentSummaryView,
  BranchInfo,
  CommitSummaryView,
  CreateWorkspaceRequest,
  DashboardSnapshotView,
  HealthView,
  SessionDetailView,
  SessionSummaryView,
  WhoamiView,
  WorkspacePaneView,
  WorkspaceStateView,
  WorkspacePeekView,
} from "./types";

/**
 * Typed protocol error mirroring the daemon's error envelope:
 *   { detail: { error: "<code>", message: "<text>" } }
 */
export class GroveProtocolError extends Error {
  override readonly name = "GroveProtocolError";
  readonly code: string;
  readonly status: number;

  constructor(code: string, message: string, status: number) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

/**
 * One client per browser. All calls go through the Next.js BFF route
 * (`/api/grove/*`); the route forwards to the daemon at GROVE_DAEMON_URL.
 */
export class GroveClient {
  static readonly _basePath = "/api/grove";

  private constructor() {}

  static default(): GroveClient {
    return new GroveClient();
  }

  async listWorkspaces(): Promise<WorkspaceStateView[]> {
    return this._get<WorkspaceStateView[]>("/workspaces");
  }

  /** Public liveness probe — minimal status + version. No auth needed. */
  async getHealth(): Promise<HealthView> {
    return this._get<HealthView>("/healthz");
  }

  /** Authenticated daemon identity + uptime. */
  async getWhoami(): Promise<WhoamiView> {
    return this._get<WhoamiView>("/whoami");
  }

  async getWorkspace(id: string): Promise<WorkspaceStateView> {
    return this._get<WorkspaceStateView>(`/workspaces/${encodeURIComponent(id)}`);
  }

  async getPeek(id: string): Promise<WorkspacePeekView> {
    return this._get<WorkspacePeekView>(`/workspaces/${encodeURIComponent(id)}/peek`);
  }

  async getCommits(id: string): Promise<CommitSummaryView[]> {
    return this._get<CommitSummaryView[]>(`/workspaces/${encodeURIComponent(id)}/commits`);
  }

  /** Recorded agent sessions for one workspace, newest-first. */
  async getSessions(id: string, limit?: number): Promise<SessionSummaryView[]> {
    const qs = limit != null ? `?limit=${limit}` : "";
    return this._get<SessionSummaryView[]>(
      `/workspaces/${encodeURIComponent(id)}/sessions${qs}`,
    );
  }

  /**
   * Recorded agent sessions across ALL of one project's worktrees,
   * newest-first — Grove-managed (workspace-attributed) and hand-staged
   * (the `workspace_*` trio null) alike. `repo` is the project's repo root,
   * the same identity the activity snapshot groups by.
   */
  async getProjectSessions(repo: string, limit?: number): Promise<SessionSummaryView[]> {
    const qs = limit != null ? `&limit=${limit}` : "";
    return this._get<SessionSummaryView[]>(`/sessions?repo=${encodeURIComponent(repo)}${qs}`);
  }

  /** One session's conversation digest — the last `last` turns, oldest-first. */
  async getSessionTurns(
    id: string,
    sessionId: string,
    last?: number,
  ): Promise<SessionDetailView> {
    const qs = last != null ? `?last=${last}` : "";
    return this._get<SessionDetailView>(
      `/workspaces/${encodeURIComponent(id)}/sessions/${encodeURIComponent(sessionId)}/turns${qs}`,
    );
  }

  /** One-shot cross-project activity snapshot — the SSE-stream fallback. */
  async getActivity(): Promise<DashboardSnapshotView> {
    return this._get<DashboardSnapshotView>("/activity");
  }

  /** One-shot agent-pane ANSI snapshot — the focused live pane's poll fallback. */
  async getPane(id: string): Promise<WorkspacePaneView> {
    return this._get<WorkspacePaneView>(`/workspaces/${encodeURIComponent(id)}/pane`);
  }

  /**
   * BFF URL for the focused live-pane SSE stream (#19). The browser opens a
   * cookie-auth `EventSource` here; the BFF pipes the daemon's diff-guarded
   * `pane_snapshot` frames. Mirrors how `useActivityStream` builds `/events`.
   */
  static paneStreamUrl(id: string): string {
    return `${GroveClient._basePath}/workspaces/${encodeURIComponent(id)}/pane/stream`;
  }

  /** Steer the workspace's agent with a follow-up message (daemon replies 204). */
  async sendMessage(id: string, text: string): Promise<void> {
    await this._post(`/workspaces/${encodeURIComponent(id)}/message`, { text });
  }

  /** Interrupt the workspace's agent (daemon replies 204; 409/501 = refusal). */
  async interrupt(id: string): Promise<void> {
    await this._post(`/workspaces/${encodeURIComponent(id)}/interrupt`);
  }

  // ─── Lifecycle mutations (workspace parity, #56) ───────────────────────────
  // These mirror the daemon's WorkspaceManager routes 1:1; the wire contract is
  // the engine's, so we send the exact request shapes and let the engine own
  // every precondition (a non-running pause, a non-OFFLINE respawn). Refusals
  // ride the same typed `GroveProtocolError` envelope as the read paths.

  /** Create a workspace from a full request (agent, title, branch plan, …). */
  async createWorkspace(req: CreateWorkspaceRequest): Promise<WorkspaceStateView> {
    return this._postJson<WorkspaceStateView>("/workspaces", req);
  }

  /**
   * Pause: tear down the tmux session, keep the branch. `force` skips the
   * dirty-worktree refusal (the engine loses uncommitted changes), so callers
   * gate it behind explicit confirmation.
   */
  async pauseWorkspace(id: string, force = false): Promise<WorkspaceStateView> {
    return this._postJson<WorkspaceStateView>(
      `/workspaces/${encodeURIComponent(id)}/pause`,
      { force },
    );
  }

  /** Resume: recreate the worktree from the retained branch (PAUSED only). */
  async resumeWorkspace(id: string): Promise<WorkspaceStateView> {
    return this._postJson<WorkspaceStateView>(`/workspaces/${encodeURIComponent(id)}/resume`);
  }

  /** Respawn: recreate the tmux session for an OFFLINE workspace (worktree intact). */
  async respawnWorkspace(id: string): Promise<WorkspaceStateView> {
    return this._postJson<WorkspaceStateView>(`/workspaces/${encodeURIComponent(id)}/respawn`);
  }

  /**
   * Kill: tear the workspace down for good (daemon replies 204). `deleteBranch`
   * null defers to the engine's `branch_provenance` default (grove-created →
   * delete, user-attached → keep); a root workspace's branch is never deleted
   * regardless. Remote branches are never touched — that stays in the user's shell.
   */
  async killWorkspace(id: string, deleteBranch: boolean | null): Promise<void> {
    await this._post(`/workspaces/${encodeURIComponent(id)}/kill`, {
      delete_branch: deleteBranch,
    });
  }

  /** Branches for one repo — populates the create form's Existing/Remote pickers. */
  async listBranches(repo: string, scope: "local" | "remote"): Promise<BranchInfo[]> {
    return this._get<BranchInfo[]>(
      `/branches?repo=${encodeURIComponent(repo)}&scope=${scope}`,
    );
  }

  /** Configured agents for one repo — populates the create form's agent picker. */
  async listAgents(repo: string): Promise<AgentSummaryView[]> {
    return this._get<AgentSummaryView[]>(`/agents?repo=${encodeURIComponent(repo)}`);
  }

  private async _get<T>(path: string): Promise<T> {
    const res = await this._fetch("GET", path);
    return (await res.json()) as T;
  }

  /** POST that expects an empty 2xx (the steering + kill endpoints reply 204). */
  private async _post(path: string, body?: unknown): Promise<void> {
    await this._fetch("POST", path, body);
  }

  /** POST that returns a JSON body (the create + pause/resume/respawn routes). */
  private async _postJson<T>(path: string, body?: unknown): Promise<T> {
    const res = await this._fetch("POST", path, body);
    return (await res.json()) as T;
  }

  private async _fetch(method: "GET" | "POST", path: string, body?: unknown): Promise<Response> {
    const url = `${GroveClient._basePath}${path}`;
    const res = await fetch(url, {
      method,
      headers: {
        accept: "application/json",
        ...(body !== undefined ? { "content-type": "application/json" } : {}),
      },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
    if (!res.ok) {
      let code = "grove_error";
      let message = `${res.status} ${res.statusText}`.trim();
      try {
        const errBody = await res.json();
        if (errBody?.detail?.error) {
          code = errBody.detail.error;
          message = errBody.detail.message ?? message;
        }
      } catch {
        // non-JSON body — keep defaults
      }
      throw new GroveProtocolError(code, message, res.status);
    }
    return res;
  }
}
