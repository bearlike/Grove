import type { AgentActivityView, AgentActivityState } from "./types";
import { humanTokens } from "./format";

/**
 * Atomic read-model over one agent session's activity view — the single home
 * for "what is this agent doing right now" derivations that the Activity
 * dashboard card and the workspace detail context bar both render. Immutable;
 * `of(null)` models a workspace with no recorded session so callers branch on
 * `hasSession` instead of threading nullable activity through their markup.
 *
 * Every accessor fails soft: a streamed view from a daemon that predates a
 * field (or carries an enum this client doesn't know) degrades to a neutral
 * value, never `undefined` — the render-hardening rule, applied at the model.
 */
export class AgentLiveStatus {
  private constructor(private readonly activity: AgentActivityView | null) {}

  static of(activity: AgentActivityView | null | undefined): AgentLiveStatus {
    return new AgentLiveStatus(activity ?? null);
  }

  /** Whether there is a recorded agent session to describe at all. */
  get hasSession(): boolean {
    return this.activity !== null;
  }

  get state(): AgentActivityState {
    return this.activity?.state ?? "unknown";
  }

  get isWorking(): boolean {
    return this.state === "working";
  }

  /** The session's model id (e.g. `claude-sonnet-4-5`), or `null` when unset/sessionless. */
  get model(): string | null {
    return this.activity?.model ?? null;
  }

  /** Error reason — populated only while the agent is in the error state. */
  get errorDetail(): string | null {
    return this.state === "error" ? (this.activity?.error_detail ?? null) : null;
  }

  /** In-flight background subagents; 0 for a pre-field daemon payload. */
  get subagents(): number {
    return this.activity?.active_subagents ?? 0;
  }

  /**
   * "Happening now": the live task wins, then the daemon's interpreted status,
   * then the session's durable self-name. `null` when nothing is known yet.
   * This precedence is the contract — keep it identical across every surface
   * that shows the live line, so the dashboard and detail page never disagree.
   */
  get taskLine(): string | null {
    if (!this.activity) return null;
    return this.activity.current_task ?? this.activity.interpreted_status ?? this.activity.title;
  }

  /** Human turn count; 0 when sessionless / for a pre-field payload. */
  get turns(): number {
    return this.activity?.human_turns ?? 0;
  }

  /** Tool calls the agent has made; 0 when sessionless / pre-field. */
  get toolCalls(): number {
    return this.activity?.tool_calls ?? 0;
  }

  /** Tokens fed to the model (context in); 0 when sessionless / pre-field. */
  get tokensIn(): number {
    return this.activity?.tokens_in ?? 0;
  }

  /** Tokens the model generated (out); 0 when sessionless / pre-field. */
  get tokensOut(): number {
    return this.activity?.tokens_out ?? 0;
  }

  /**
   * Muted metrics one-liner (turns · tools · tokens); `null` when sessionless.
   * The card renders these as discrete `Stat` atoms now (#161); this composite
   * string survives for the work panel's Info tab, which reads as one dense line.
   */
  get metricsLine(): string | null {
    if (!this.activity) return null;
    const a = this.activity;
    return `${a.human_turns}t · ${a.tool_calls}⚒ · ${humanTokens(a.tokens_in)}↑ ${humanTokens(a.tokens_out)}↓`;
  }
}
