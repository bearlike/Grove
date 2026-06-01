import type {
  CommitSummaryView,
  HealthView,
  WhoamiView,
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

  private async _get<T>(path: string): Promise<T> {
    const url = `${GroveClient._basePath}${path}`;
    const res = await fetch(url, {
      method: "GET",
      headers: { accept: "application/json" },
    });
    if (!res.ok) {
      let code = "grove_error";
      let message = `${res.status} ${res.statusText}`.trim();
      try {
        const body = await res.json();
        if (body?.detail?.error) {
          code = body.detail.error;
          message = body.detail.message ?? message;
        }
      } catch {
        // non-JSON body — keep defaults
      }
      throw new GroveProtocolError(code, message, res.status);
    }
    return (await res.json()) as T;
  }
}
