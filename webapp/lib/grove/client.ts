import type { QuestionAnswerItem } from "./question-plan";
import type {
  AgentSummaryView,
  BranchInfo,
  CommitSummaryView,
  CreateWorkspaceRequest,
  DashboardSnapshotView,
  HealthView,
  ProvisionProgressView,
  SessionControlsView,
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

  /**
   * Recorded agent sessions for one workspace, newest-first. Default is the
   * daemon's ATTRIBUTED history (adoption-gated). `candidates: true` flips the
   * scan to the UNGATED cwd-scoped set — the remap-picker seam that KEEPS
   * the sessions the gate drops (a dead-minted-pointer's live successor, a
   * foreign session in a shared ROOT cwd) so a UI can offer them to pin.
   */
  async getSessions(
    id: string,
    opts?: { limit?: number; candidates?: boolean },
  ): Promise<SessionSummaryView[]> {
    const params = new URLSearchParams();
    if (opts?.limit != null) params.set("limit", String(opts.limit));
    if (opts?.candidates) params.set("candidates", "true");
    const qs = params.toString();
    return this._get<SessionSummaryView[]>(
      `/workspaces/${encodeURIComponent(id)}/sessions${qs ? `?${qs}` : ""}`,
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

  /**
   * The host-wide Session Catalog — the SAME `GET /sessions` route with
   * `repo` OMITTED. Not a variant of `getProjectSessions`: omitting `repo`
   * widens the scope to every session in every adapter's store, including repos
   * Grove has never managed, and swaps the cost model (one bounded head read per
   * session, no transcript parse) — so rows carry `project`/`cwd`/`live` but
   * leave `activity`/`size_bytes` null. `limit` is daemon-bounded to 200.
   */
  async getSessionCatalog(limit?: number): Promise<SessionSummaryView[]> {
    const qs = limit != null ? `?limit=${limit}` : "";
    return this._get<SessionSummaryView[]>(`/sessions${qs}`);
  }

  /**
   * A catalog row's conversation — the workspace-LESS drill-in. Resolves
   * by the `(kind, cwd, session_id)` coordinate a catalog row carries, because
   * most sessions on a host were never launched by Grove and so cannot be
   * reached through a workspace. `cwd` MUST be the exact string the row
   * reported: the adapters match a recorded cwd byte-for-byte. A row with no
   * `cwd` is not drillable at all — callers must not call this for one.
   */
  async getCatalogTurns(
    sessionId: string,
    kind: string,
    cwd: string,
    last?: number,
  ): Promise<SessionDetailView> {
    const params = new URLSearchParams({ kind, cwd });
    if (last != null) params.set("last", String(last));
    return this._get<SessionDetailView>(
      `/sessions/${encodeURIComponent(sessionId)}/turns?${params.toString()}`,
    );
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

  /**
   * The session's available input controls — enumerated slash commands,
   * skills, MCP servers, the model catalog + current model, permission posture.
   * Fetch-on-demand (never SSE); read-only display is the core value. Best-effort
   * daemon-side: an agent with no control surface yields empty lists, not an error.
   */
  async getControls(id: string): Promise<SessionControlsView> {
    return this._get<SessionControlsView>(`/workspaces/${encodeURIComponent(id)}/controls`);
  }

  /**
   * Invoke a named session control — a slash command or a skill. The
   * daemon composes `/name` and delivers it through the steer path (204 on
   * dispatch — "delivered", not "ran"; the result rides the transcript later).
   * Refusals: 501 `capability_unavailable` (a shell/remote kind), 409 pane/state.
   */
  async invokeControl(id: string, name: string): Promise<void> {
    await this._post(`/workspaces/${encodeURIComponent(id)}/controls/invoke`, { name });
  }

  /**
   * Switch the running session's model — delivered as the interactive
   * `/model <id>` control. `model` is forwarded verbatim (the provider boundary).
   * Refusals mirror `invokeControl`.
   */
  async switchModel(id: string, model: string): Promise<void> {
    await this._post(`/workspaces/${encodeURIComponent(id)}/controls/model`, { model });
  }

  /**
   * Live progress of an in-flight container provision — elapsed time, the last
   * line the provisioner wrote, and a bounded tail of the build log.
   *
   * Fetch-on-demand, never SSE: the state view already streams *whether* a
   * workspace is provisioning, and re-emitting a log tail for every workspace on
   * every activity tick would cost a file read per workspace per tick. Callers
   * poll this only while the workspace's status is `provisioning`.
   */
  async getProvisionProgress(id: string): Promise<ProvisionProgressView> {
    return this._get<ProvisionProgressView>(
      `/workspaces/${encodeURIComponent(id)}/provision`,
    );
  }

  /** One-shot agent-pane ANSI snapshot — the focused live pane's poll fallback. */
  async getPane(id: string): Promise<WorkspacePaneView> {
    return this._get<WorkspacePaneView>(`/workspaces/${encodeURIComponent(id)}/pane`);
  }

  /**
   * BFF URL for the focused live-pane SSE stream. The browser opens a
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

  /**
   * Answer a live pending `AskUserQuestion` — POST
   * `/workspaces/{id}/question-answer`, 204 on dispatch. This is "dispatched",
   * not "resolved": the daemon drives the terminal's keystrokes and the actual
   * resolution rides back later via the SSE-carried `questions` list emptying,
   * not this response. `toolUseId` is the pending question's
   * `group_id` — the native tool call every question in the batch shares.
   * Refusals: 404 unknown workspace, 409 `tool_use_id` no longer pending
   * (stale — already resolved or cancelled elsewhere), 422 the answer plan
   * didn't validate against the captured questions.
   */
  async answerQuestion(
    id: string,
    sessionId: string,
    toolUseId: string,
    answers: QuestionAnswerItem[],
  ): Promise<void> {
    await this._post(`/workspaces/${encodeURIComponent(id)}/question-answer`, {
      session_id: sessionId,
      tool_use_id: toolUseId,
      answers,
    });
  }

  // ─── Lifecycle mutations (workspace parity) ────────────────────────────────
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

  /**
   * Pin an existing agent session as this workspace's tracked primary —
   * POST `/workspaces/{id}/session`, 200 with the updated `WorkspaceStateView`.
   * `sessionRef` is a full session id or a unique id-prefix, resolved in the
   * workspace's project scope (engine-side, like `grove sessions show`).
   * Refusals: 404 `workspace_not_found` / `agent_session_not_found` (the ref
   * resolves nowhere, or is ambiguous), 409 `workspace_state_error` (ORPHANED).
   */
  async remapSession(id: string, sessionRef: string): Promise<WorkspaceStateView> {
    return this._postJson<WorkspaceStateView>(`/workspaces/${encodeURIComponent(id)}/session`, {
      session_ref: sessionRef,
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
        const detail = errBody?.detail;
        if (detail && typeof detail === "object" && !Array.isArray(detail) && "error" in detail) {
          // The daemon's own envelope: { detail: { error, message } }.
          code = detail.error;
          message = detail.message ?? message;
        } else if (Array.isArray(detail) && detail.length > 0) {
          // FastAPI's request-validation 422s (e.g. Pydantic's title max_length)
          // ship a LIST of {loc, msg, type} instead — no daemon envelope at all.
          // Join the first couple entries into a readable "field: reason" line.
          code = "validation_error";
          message = (detail as Array<{ loc?: unknown[]; msg?: string }>)
            .slice(0, 2)
            .map((d) => `${d.loc?.at(-1) ?? "body"}: ${d.msg ?? "invalid"}`)
            .join("; ");
        }
      } catch {
        // non-JSON body — keep defaults
      }
      throw new GroveProtocolError(code, message, res.status);
    }
    return res;
  }
}
