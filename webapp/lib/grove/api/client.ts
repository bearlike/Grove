import type {
  AgentSummaryView,
  AttachmentView,
  BranchInfo,
  CommitSummaryView,
  CreateWorkspaceRequest,
  DashboardSnapshotView,
  HealthView,
  ModelOptionView,
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
  WorkspaceHistoryView,
  WorkspacePaneView,
  WorkspacePeekView,
  WorkspaceQueueView,
  WorkspaceStateView,
  GalleryDocumentView,
  GalleryItemView,
  GalleryPreviewUploadRequest,
  GalleryPreviewView,
} from "./types";
import type { WorkspacePanelView } from "./panels";
import type {
  DiagramDocumentView,
  DiagramOpenRequest,
  DiagramPreviewUploadRequest,
  DiagramPreviewView,
  DiagramStopRequest,
  DiagramUpdateRequest,
} from "./diagrams";
import type { components } from "./types.gen";

/**
 * Base64 for a byte array, in chunks.
 *
 * The one-liner — `btoa(String.fromCharCode(...bytes))` — spreads one argument
 * per byte and overflows the call stack far below the 32 MiB the daemon
 * accepts, so it fails on exactly the files the limit was written for. Chunking
 * is the standard escape. Exported because it is the only pure part of an
 * upload and therefore the only part worth pinning.
 */
export function base64FromBytes(bytes: Uint8Array): string {
  const CHUNK = 0x8000;
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += CHUNK) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + CHUNK));
  }
  return btoa(binary);
}

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
type SharePolicyUpdateRequest =
  components["schemas"]["SharePolicyUpdateRequest"];
type SendKey = components["schemas"]["SendKey"];
type SendKeysRequest = components["schemas"]["SendKeysRequest"];
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
  /**
   * What the agent's own control surface measured and its transcript cannot say
   * — a command's real exit code and duration, the model's context window.
   *
   * `/todo`'s sibling in refusal (404 `agent_session_not_found`) but NOT in cost:
   * this spawns a subprocess daemon-side, so it is read on open and on demand and
   * is deliberately given no poll interval. `supported: false` is the ordinary
   * answer — only a Codex agent opted into `app_server` reports anything, and
   * Claude Code publishes no such surface at all.
   */
  /**
   * Every name this workspace has held, every progress claim it reported, and
   * every ticket it was attached to — from a store that outlives the workspace.
   *
   * An EMPTY view is the ordinary answer, not a failure: recording is
   * forward-only, so a workspace created before this shipped has nothing. The
   * 404 means the workspace itself is unknown, exactly like `/todo`'s.
   *
   * Read on demand and given no poll interval. Nothing on `/events` carries
   * these rows, but they only change when the agent reports a NEW claim — and
   * the claim it is reporting right now is already on the Task card, live.
   */
  async getWorkspaceHistory(id: string): Promise<WorkspaceHistoryView> {
    return this.get(`/workspaces/${encodeURIComponent(id)}/history`);
  }
  async getWorkspacePanels(id: string): Promise<WorkspacePanelView[]> {
    return this.get(`/workspaces/${encodeURIComponent(id)}/panels`);
  }
  /**
   * The workspace's diagram — the latest bytes ON DISK, never a browser draft.
   *
   * 404s when the workspace has no descriptor or the file has gone; the tab is
   * mounted from the descriptor on the workspace record, so that is a real
   * state rather than a race.
   */
  /**
   * `signal` is how a read is SEQUENCED against a write.
   *
   * A GET issued before a PUT answers after it with pre-write bytes, and no
   * amount of comparing revisions afterwards can tell that apart from somebody
   * else having written the file — the two are identical on the wire. The
   * caller therefore cancels the in-flight read before it writes, so the stale
   * answer is never delivered rather than being detected and discarded.
   */
  async getDiagram(
    id: string,
    repo: string,
    signal?: AbortSignal,
  ): Promise<DiagramDocumentView> {
    return this.get(`${this.diagramPath(id)}${this.repoSuffix(repo)}`, signal);
  }
  async openDiagram(
    id: string,
    repo: string,
    request: DiagramOpenRequest,
  ): Promise<DiagramDocumentView> {
    return this.postJson(
      `${this.diagramPath(id)}${this.repoSuffix(repo)}`,
      request,
    );
  }
  /**
   * Replace the file, only if `expected_revision` is still what is on disk.
   *
   * Conditional by construction: a stale revision answers 409 and writes
   * nothing, which is what makes two browsers — or a browser and an agent —
   * safe without a merge. `session_id` fences a write issued before a stop or a
   * reopen retired that collaboration.
   */
  async updateDiagram(
    id: string,
    repo: string,
    request: DiagramUpdateRequest,
  ): Promise<DiagramDocumentView> {
    return this.putJson(
      `${this.diagramPath(id)}${this.repoSuffix(repo)}`,
      request,
    );
  }
  async stopDiagram(
    id: string,
    repo: string,
    request: DiagramStopRequest,
  ): Promise<DiagramDocumentView> {
    return this.postJson(
      `${this.diagramPath(id)}/stop${this.repoSuffix(repo)}`,
      request,
    );
  }
  /** A rendered first page is keyed to the exact acknowledged document revision. */
  async saveDiagramPreview(
    id: string,
    request: DiagramPreviewUploadRequest,
  ): Promise<DiagramPreviewView> {
    return this.postJson(`${this.diagramPath(id)}/preview`, request);
  }
  async getDiagramPreview(id: string): Promise<DiagramPreviewView> {
    return this.get(`${this.diagramPath(id)}/preview`);
  }
  /**
   * The host-wide diagram gallery: every `.drawio` in a known repo's worktrees.
   * Items are addressed by an opaque id the daemon minted — never a path.
   */
  async getGallery(): Promise<GalleryItemView[]> {
    return this.get("/gallery");
  }
  async getGalleryDocument(id: string): Promise<GalleryDocumentView> {
    return this.get(`/gallery/${encodeURIComponent(id)}`);
  }
  async getGalleryPreview(id: string): Promise<GalleryPreviewView> {
    return this.get(`/gallery/${encodeURIComponent(id)}/preview`);
  }
  /** A first-page PNG the browser rendered, fenced to the content digest it depicts. */
  async saveGalleryPreview(
    id: string,
    request: GalleryPreviewUploadRequest,
  ): Promise<GalleryPreviewView> {
    return this.postJson(`/gallery/${encodeURIComponent(id)}/preview`, request);
  }
  /**
   * `repo` is REQUIRED on every diagram route, unlike the other workspace
   * reads on this client.
   *
   * The daemon resolves the configured project before it touches the
   * filesystem and refuses a workspace identity that does not belong to it, so
   * the parameter is part of the check rather than a convenience.
   */
  private repoSuffix(repo: string): string {
    return `?repo=${encodeURIComponent(repo)}`;
  }
  private diagramPath(id: string): string {
    return `/workspaces/${encodeURIComponent(id)}/diagram`;
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
  /**
   * Steer the agent, optionally naming files already stored by
   * {@link uploadAttachment}.
   *
   * `attachments` carries IDS, never paths: the engine appends the block that
   * names each file and resolves the path the agent will actually read it at,
   * which for a containerized workspace is not a path this client has ever
   * seen.
   */
  async sendMessage(
    id: string,
    text: string,
    attachments: string[] = [],
  ): Promise<void> {
    return this.post(`/workspaces/${encodeURIComponent(id)}/message`, {
      text,
      attachments,
    });
  }

  /**
   * Store one file in the workspace's attachment directory.
   *
   * The bytes cross as base64 inside JSON, which the daemon's own contract
   * argues for from the other side and this side confirms: the BFF proxy at
   * `app/api/grove/[...path]` reads every request body as text, so a multipart
   * binary body would arrive corrupted. Uploading is its own request precisely
   * so a file that fails does not take a typed message down with it.
   */
  async uploadAttachment(
    id: string,
    name: string,
    file: Blob,
  ): Promise<AttachmentView> {
    const bytes = new Uint8Array(await file.arrayBuffer());
    return this.postJson(`/workspaces/${encodeURIComponent(id)}/attachments`, {
      name,
      content_base64: base64FromBytes(bytes),
    });
  }
  async interrupt(id: string): Promise<void> {
    return this.post(`/workspaces/${encodeURIComponent(id)}/interrupt`);
  }
  /** Deliver one named tmux key to the workspace's live agent pane. */
  async sendKey(id: string, key: SendKey): Promise<void> {
    const request: SendKeysRequest = { key };
    return this.post(`/workspaces/${encodeURIComponent(id)}/keys`, request);
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
  /**
   * One agent's model catalog with the name and context window each row draws.
   *
   * The enriched read beside `listAgents`, whose `models` stays a tuple of bare
   * ids. `agent` omitted asks for the repo's first configured agent, which is
   * what an untouched create form has selected.
   */
  async listModels(repo: string, agent?: string | null): Promise<ModelOptionView[]> {
    const query = new URLSearchParams({ repo });
    if (agent) query.set("agent", agent);
    return this.get(`/models${this.suffix(query)}`);
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
    return this.putJson(
      `/share-policy?repo=${encodeURIComponent(repo)}`,
      policy,
    );
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

  private async get<T>(path: string, signal?: AbortSignal): Promise<T> {
    return this.json<T>(await this.fetch("GET", path, undefined, signal));
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
    signal?: AbortSignal,
  ): Promise<Response> {
    const response = await fetch(`${GroveClient.basePath}${path}`, {
      method,
      ...(signal === undefined ? {} : { signal }),
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
