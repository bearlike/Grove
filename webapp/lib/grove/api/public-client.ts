import type { SessionDetailView, WorkspaceDiffView } from "./types";
import type { components } from "./types.gen";
import { GroveProtocolError } from "./client";
import { SHARE_PASSCODE_HEADER, readSharePasscode } from "./share-passcode";

export type PublicWorkspaceView = components["schemas"]["PublicWorkspaceView"];

type ErrorEnvelope = { detail?: unknown };
type DaemonErrorDetail = { error: string; message?: string };
type ValidationDetail = { loc?: unknown[]; msg?: string };

/** A deliberately narrow client for the bearer-free public BFF. */
export class PublicClient {
  static readonly basePath = "/api/public";

  static create(): PublicClient {
    return new PublicClient();
  }

  async overview(token: string): Promise<PublicWorkspaceView> {
    return this.get(`/${encodeURIComponent(token)}`, token);
  }

  async turns(
    token: string,
    options: { last?: number; afterTurn?: number } = {},
  ): Promise<SessionDetailView | null> {
    const query = new URLSearchParams();
    if (options.last !== undefined) query.set("last", String(options.last));
    if (options.afterTurn !== undefined)
      query.set("after_turn", String(options.afterTurn));
    return this.get(
      `/${encodeURIComponent(token)}/turns${this.suffix(query)}`,
      token,
    );
  }

  async diff(token: string, path?: string): Promise<WorkspaceDiffView> {
    const query = new URLSearchParams();
    if (path !== undefined) query.set("path", path);
    return this.get(
      `/${encodeURIComponent(token)}/diff${this.suffix(query)}`,
      token,
    );
  }

  private suffix(query: URLSearchParams): string {
    const encoded = query.toString();
    return encoded ? `?${encoded}` : "";
  }

  /**
   * Every read attaches the passcode for ITS OWN token, if the reader has
   * supplied one.
   *
   * Taken here rather than passed down from each caller so no route can forget
   * it — the same reason the daemon folds its check into the one place a reader
   * is constructed. A project with no passcode sends no header and behaves
   * exactly as before.
   */
  private async get<T>(path: string, token: string): Promise<T> {
    const passcode = readSharePasscode(token);
    const response = await fetch(`${PublicClient.basePath}${path}`, {
      headers: {
        accept: "application/json",
        ...(passcode ? { [SHARE_PASSCODE_HEADER]: passcode } : {}),
      },
    });
    if (!response.ok) throw await this.protocolError(response);
    return (await response.json()) as T;
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

/** Public hooks share one stateless client for stable query functions. */
export const publicClient: PublicClient = PublicClient.create();
