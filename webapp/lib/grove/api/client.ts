import type {
  AgentSummaryView,
  BranchInfo,
  CommitSummaryView,
  CreateWorkspaceRequest,
  DashboardSnapshotView,
  HealthView,
  ProvisionProgressView,
  QuestionAnswerItem,
  SessionControlsView,
  SessionDetailView,
  SessionSummaryView,
  TicketProviderView,
  TicketRef,
  TodoListView,
  UpdateWorkspaceRequest,
  UsageActivityView,
  UsageBashInsightView,
  UsageBreakdownView,
  UsageFindingsView,
  UsageQuotasView,
  UsageRefreshView,
  UsageSeriesView,
  UsageSessionPageView,
  UsageSummaryView,
  WhoamiView,
  WorkspaceDiffView,
  WorkspacePaneView,
  WorkspacePeekView,
  WorkspaceQueueView,
  WorkspaceStateView,
} from "./types";
import type { components } from "./types.gen";

export class GroveProtocolError extends Error {
  override readonly name = "GroveProtocolError";

  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

type HttpMethod = "GET" | "POST" | "PUT" | "PATCH";
type UsageFilters = Record<string, string>;
type WorkspaceDefaultsView = components["schemas"]["WorkspaceDefaultsView"];
type WorkspaceDefaultsSaveView =
  components["schemas"]["WorkspaceDefaultsSaveView"];
type DefaultsScope = components["schemas"]["DefaultsScope"];
type SharePolicyView = components["schemas"]["SharePolicyView"];
type SharePolicyUpdateRequest = components["schemas"]["SharePolicyUpdateRequest"];
type SessionOptions = { limit?: number; candidates?: boolean };
type UsageSessionOptions = {
  sort: string;
  limit: number;
  cursor?: string | null;
};
type ErrorEnvelope = { detail?: unknown };
type DaemonErrorDetail = { error: string; message?: string };
type ValidationDetail = { loc?: unknown[]; msg?: string };

/** The browser-side daemon client; requests use the cookie-authenticated BFF. */
export class GroveClient {
  static readonly basePath = "/api/grove";

  static create(): GroveClient {
    return new GroveClient();
  }

  static paneStreamUrl(id: string): string {
    return `${GroveClient.basePath}/workspaces/${encodeURIComponent(id)}/pane/stream`;
  }

  async listWorkspaces(): Promise<WorkspaceStateView[]> {
    return this.get("/workspaces");
  }
  async getHealth(): Promise<HealthView> {
    return this.get("/healthz");
  }
  async getWhoami(): Promise<WhoamiView> {
    return this.get("/whoami");
  }
  async getWorkspace(id: string): Promise<WorkspaceStateView> {
    return this.get(`/workspaces/${encodeURIComponent(id)}`);
  }
  async getPeek(id: string): Promise<WorkspacePeekView> {
    return this.get(`/workspaces/${encodeURIComponent(id)}/peek`);
  }
  /** The harness's own steer queue — bounded, fetch-on-demand; the ~1 Hz tick carries only its depth. */
  async getQueue(id: string): Promise<WorkspaceQueueView> {
    return this.get(`/workspaces/${encodeURIComponent(id)}/queue`);
  }
  /**
   * The agent's current plan/checklist — fetch-on-demand; 404s
   * `agent_session_not_found` when there is no session at all.
   *
   * A todo write is a full REWRITE, so this route is the whole answer — and it
   * is NOT derivable from the transcript any more, whose windowed tail
   * routinely contains no todo entry at all.
   */
  async getWorkspaceTodo(id: string): Promise<TodoListView> {
    return this.get(`/workspaces/${encodeURIComponent(id)}/todo`);
  }
  async getCommits(id: string): Promise<CommitSummaryView[]> {
    return this.get(`/workspaces/${encodeURIComponent(id)}/commits`);
  }
  async getDiff(id: string): Promise<WorkspaceDiffView> {
    return this.get(`/workspaces/${encodeURIComponent(id)}/diff`);
  }

  async getSessions(
    id: string,
    options?: SessionOptions,
  ): Promise<SessionSummaryView[]> {
    const query = new URLSearchParams();
    if (options?.limit !== undefined) query.set("limit", String(options.limit));
    if (options?.candidates) query.set("candidates", "true");
    return this.get(
      `/workspaces/${encodeURIComponent(id)}/sessions${this.suffix(query)}`,
    );
  }

  async getProjectSessions(
    repo: string,
    limit?: number,
  ): Promise<SessionSummaryView[]> {
    const query = new URLSearchParams({ repo });
    if (limit !== undefined) query.set("limit", String(limit));
    return this.get(`/sessions${this.suffix(query)}`);
  }

  async getSessionCatalog(limit?: number): Promise<SessionSummaryView[]> {
    const query = new URLSearchParams();
    if (limit !== undefined) query.set("limit", String(limit));
    return this.get(`/sessions${this.suffix(query)}`);
  }

  async getCatalogTurns(
    sessionId: string,
    kind: string,
    cwd: string,
    last?: number,
  ): Promise<SessionDetailView> {
    const query = new URLSearchParams({ kind, cwd });
    if (last !== undefined) query.set("last", String(last));
    return this.get(
      `/sessions/${encodeURIComponent(sessionId)}/turns${this.suffix(query)}`,
    );
  }

  /**
   * One session's transcript, whole or as a resumption window.
   *
   * `afterTurn` is the INCLUSIVE ordinal a live follower resumes from; the
   * response says whether the daemon honoured it. The two options are mutually
   * exclusive on the wire (422), so they are one object rather than two
   * positional arguments a call site could pass together by accident.
   */
  async getSessionTurns(
    id: string,
    sessionId: string,
    options: { last?: number; afterTurn?: number } = {},
  ): Promise<SessionDetailView> {
    const query = new URLSearchParams();
    if (options.last !== undefined) query.set("last", String(options.last));
    if (options.afterTurn !== undefined)
      query.set("after_turn", String(options.afterTurn));
    return this.get(
      `/workspaces/${encodeURIComponent(id)}/sessions/${encodeURIComponent(sessionId)}/turns${this.suffix(query)}`,
    );
  }

  async getActivity(): Promise<DashboardSnapshotView> {
    return this.get("/activity");
  }
  async getControls(id: string): Promise<SessionControlsView> {
    return this.get(`/workspaces/${encodeURIComponent(id)}/controls`);
  }
  async getProvisionProgress(id: string): Promise<ProvisionProgressView> {
    return this.get(`/workspaces/${encodeURIComponent(id)}/provision`);
  }
  async getPane(id: string): Promise<WorkspacePaneView> {
    return this.get(`/workspaces/${encodeURIComponent(id)}/pane`);
  }
  async invokeControl(id: string, name: string): Promise<void> {
    return this.post(`/workspaces/${encodeURIComponent(id)}/controls/invoke`, {
      name,
    });
  }
  async switchModel(id: string, model: string): Promise<void> {
    return this.post(`/workspaces/${encodeURIComponent(id)}/controls/model`, {
      model,
    });
  }
  async sendMessage(id: string, text: string): Promise<void> {
    return this.post(`/workspaces/${encodeURIComponent(id)}/message`, { text });
  }
  async interrupt(id: string): Promise<void> {
    return this.post(`/workspaces/${encodeURIComponent(id)}/interrupt`);
  }

  async answerQuestion(
    id: string,
    sessionId: string,
    toolUseId: string,
    answers: QuestionAnswerItem[],
  ): Promise<void> {
    return this.post(`/workspaces/${encodeURIComponent(id)}/question-answer`, {
      session_id: sessionId,
      tool_use_id: toolUseId,
      answers,
    });
  }

  async createWorkspace(
    request: CreateWorkspaceRequest,
  ): Promise<WorkspaceStateView> {
    return this.postJson("/workspaces", request);
  }
  async pauseWorkspace(id: string, force = false): Promise<WorkspaceStateView> {
    return this.postJson(`/workspaces/${encodeURIComponent(id)}/pause`, {
      force,
    });
  }
  async resumeWorkspace(id: string): Promise<WorkspaceStateView> {
    return this.postJson(`/workspaces/${encodeURIComponent(id)}/resume`);
  }
  async respawnWorkspace(id: string): Promise<WorkspaceStateView> {
    return this.postJson(`/workspaces/${encodeURIComponent(id)}/respawn`);
  }
  async killWorkspace(id: string, deleteBranch: boolean | null): Promise<void> {
    return this.post(`/workspaces/${encodeURIComponent(id)}/kill`, {
      delete_branch: deleteBranch,
    });
  }
  async remapSession(
    id: string,
    sessionRef: string,
  ): Promise<WorkspaceStateView> {
    return this.postJson(`/workspaces/${encodeURIComponent(id)}/session`, {
      session_ref: sessionRef,
    });
  }
  async updateWorkspace(
    id: string,
    request: UpdateWorkspaceRequest,
  ): Promise<WorkspaceStateView> {
    return this.patchJson(`/workspaces/${encodeURIComponent(id)}`, request);
  }
  async listBranches(
    repo: string,
    scope: "local" | "remote",
  ): Promise<BranchInfo[]> {
    return this.get(
      `/branches?repo=${encodeURIComponent(repo)}&scope=${scope}`,
    );
  }
  async listAgents(repo: string): Promise<AgentSummaryView[]> {
    return this.get(`/agents?repo=${encodeURIComponent(repo)}`);
  }
  async getWorkspaceDefaults(repo: string): Promise<WorkspaceDefaultsView> {
    return this.get(`/defaults?repo=${encodeURIComponent(repo)}`);
  }
  async saveWorkspaceDefaults(
    defaults: WorkspaceDefaultsView,
    scope: DefaultsScope,
    repo?: string,
  ): Promise<WorkspaceDefaultsSaveView> {
    const query = new URLSearchParams({ scope });
    if (repo) query.set("repo", repo);
    return this.putJson(`/defaults${this.suffix(query)}`, defaults);
  }
  async getSharePolicy(repo: string): Promise<SharePolicyView> {
    return this.get(`/share-policy?repo=${encodeURIComponent(repo)}`);
  }
  async saveSharePolicy(
    repo: string,
    policy: SharePolicyUpdateRequest,
  ): Promise<SharePolicyView> {
    return this.putJson(`/share-policy?repo=${encodeURIComponent(repo)}`, policy);
  }

  /** The trackers this repo may be asked about; `configured` is the credentials signal. */
  async listTicketProviders(repo: string): Promise<TicketProviderView[]> {
    return this.get(`/tickets/providers?repo=${encodeURIComponent(repo)}`);
  }

  /**
   * Open tickets assigned to whoever the repo's tracker credential authenticates as.
   *
   * Gate this on `configured` from `listTicketProviders` before calling it: an
   * unconfigured provider has no credential, so "who am I" has no answer and the
   * call can only fail. Not offering the suggestion is the honest response to
   * that, which is why the check belongs at the call site rather than here.
   */
  async listAssignedTickets(
    repo: string,
    provider?: string,
    status?: string,
  ): Promise<TicketRef[]> {
    const query = new URLSearchParams({ repo });
    if (provider) query.set("provider", provider);
    if (status) query.set("status", status);
    return this.get(`/tickets/assigned${this.suffix(query)}`);
  }

  /**
   * One stored ref resolved against the live tracker.
   *
   * `provider` is the wire enum rather than a bare string so a caller cannot
   * invent a route the daemon has no adapter for.
   */
  async getTicket(
    repo: string,
    provider: TicketRef["provider"],
    ticketId: string,
  ): Promise<TicketRef> {
    const path = `/tickets/${provider}/${encodeURIComponent(ticketId)}`;
    return this.get(`${path}?repo=${encodeURIComponent(repo)}`);
  }

  async getUsageSummary(filters: UsageFilters): Promise<UsageSummaryView> {
    return this.get(
      `/usage/summary${this.suffix(new URLSearchParams(filters))}`,
    );
  }
  async getUsageActivity(
    filters: UsageFilters,
    metric: string,
  ): Promise<UsageActivityView> {
    return this.get(
      `/usage/activity${this.suffix(new URLSearchParams({ ...filters, metric }))}`,
    );
  }
  async getUsageSessions(
    filters: UsageFilters,
    options: UsageSessionOptions,
  ): Promise<UsageSessionPageView> {
    const query = new URLSearchParams({
      ...filters,
      sort: options.sort,
      limit: String(options.limit),
    });
    if (options.cursor) query.set("cursor", options.cursor);
    return this.get(`/usage/sessions${this.suffix(query)}`);
  }
  async getUsageBreakdown(
    filters: UsageFilters,
    dimension: string,
  ): Promise<UsageBreakdownView> {
    return this.get(
      `/usage/breakdowns${this.suffix(new URLSearchParams({ ...filters, dimension }))}`,
    );
  }
  async getUsageSeries(
    filters: UsageFilters,
    dimension: string,
    metric: string,
  ): Promise<UsageSeriesView> {
    return this.get(
      `/usage/series${this.suffix(new URLSearchParams({ ...filters, dimension, metric }))}`,
    );
  }
  async getUsageQuotas(): Promise<UsageQuotasView> {
    return this.get("/usage/quotas");
  }
  async getUsageFindings(filters: UsageFilters): Promise<UsageFindingsView> {
    return this.get(
      `/usage/findings${this.suffix(new URLSearchParams(filters))}`,
    );
  }
  async getUsageBashCommands(
    filters: UsageFilters,
  ): Promise<UsageBashInsightView> {
    return this.get(
      `/usage/bash-commands${this.suffix(new URLSearchParams(filters))}`,
    );
  }
  async refreshUsage(): Promise<UsageRefreshView> {
    return this.postJson("/usage/refresh");
  }

  private suffix(query: URLSearchParams): string {
    const value = query.toString();
    return value ? `?${value}` : "";
  }

  private async get<T>(path: string): Promise<T> {
    return this.json<T>(await this.fetch("GET", path));
  }
  private async post(path: string, body?: unknown): Promise<void> {
    await this.fetch("POST", path, body);
  }
  private async postJson<T>(path: string, body?: unknown): Promise<T> {
    return this.json<T>(await this.fetch("POST", path, body));
  }
  private async putJson<T>(path: string, body: unknown): Promise<T> {
    return this.json<T>(await this.fetch("PUT", path, body));
  }
  private async patchJson<T>(path: string, body: unknown): Promise<T> {
    return this.json<T>(await this.fetch("PATCH", path, body));
  }
  private async json<T>(response: Response): Promise<T> {
    return (await response.json()) as T;
  }

  private async fetch(
    method: HttpMethod,
    path: string,
    body?: unknown,
  ): Promise<Response> {
    const response = await fetch(`${GroveClient.basePath}${path}`, {
      method,
      headers: {
        accept: "application/json",
        ...(body === undefined ? {} : { "content-type": "application/json" }),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    if (!response.ok) throw await this.protocolError(response);
    return response;
  }

  private async protocolError(response: Response): Promise<GroveProtocolError> {
    let code = "grove_error";
    let message = `${response.status} ${response.statusText}`.trim();
    try {
      const body = (await response.json()) as ErrorEnvelope;
      if (this.isDaemonError(body.detail)) {
        code = body.detail.error;
        message = body.detail.message ?? message;
      } else if (Array.isArray(body.detail) && body.detail.length > 0) {
        code = "validation_error";
        message = body.detail
          .slice(0, 2)
          .filter(this.isValidationDetail)
          .map(
            (detail) =>
              `${detail.loc?.at(-1) ?? "body"}: ${detail.msg ?? "invalid"}`,
          )
          .join("; ");
      }
    } catch {
      /* Preserve the HTTP status when an intermediary returned non-JSON. */
    }
    return new GroveProtocolError(code, message, response.status);
  }

  private isDaemonError(value: unknown): value is DaemonErrorDetail {
    return (
      typeof value === "object" &&
      value !== null &&
      !Array.isArray(value) &&
      "error" in value &&
      typeof value.error === "string"
    );
  }

  private isValidationDetail(value: unknown): value is ValidationDetail {
    return typeof value === "object" && value !== null && !Array.isArray(value);
  }
}
