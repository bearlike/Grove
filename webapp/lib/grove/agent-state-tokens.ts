import type { AgentActivityState } from "./types";

/**
 * Agent-activity-state hex — dark mode. Mirrors the cross-client contract
 * `grove.core.contracts.agent_palette.DARK_AGENT_STATE_HEX` (the same source the
 * TUI reads). A *separate* axis from the workspace-status palette: status colors
 * what the workspace is, this colors what the agent session inside it is doing.
 * Drift caught by tests/unit/agent-state-tokens.test.ts.
 */
export const AGENT_STATE_HEX_DARK: Record<AgentActivityState, string> = {
  starting: "#c2dcf7", // info cyan — spinning up
  working: "#84cc16", // lime (matches ACTIVE) — live signal
  waiting: "#b8860b", // warning amber — wants the human
  blocked: "#b8860b", // warning amber — explicit prompt
  idle: "#96938c", // muted gray — alive but quiet
  error: "#e64c4c", // destructive red — failed / unreadable
  unknown: "#96938c", // muted gray — no signal
};

/**
 * Agent-state hex — light mode. Tuned for browser white/slate surfaces, same
 * intent as the dark map (the status-tokens light values are the precedent).
 */
export const AGENT_STATE_HEX_LIGHT: Record<AgentActivityState, string> = {
  starting: "#2563eb",
  working: "#65a30d",
  waiting: "#b45309",
  blocked: "#b45309",
  idle: "#71717a",
  error: "#b91c1c",
  unknown: "#71717a",
};

/** One-char glyph per state — mirrors AGENT_STATE_GLYPH in grove/tui/_status.py. */
export const AGENT_STATE_GLYPH: Record<AgentActivityState, string> = {
  starting: "◌",
  working: "▶",
  waiting: "◑",
  blocked: "⚠",
  idle: "○",
  error: "✗",
  unknown: "·",
};

export const AGENT_STATE_LABEL: Record<AgentActivityState, string> = {
  starting: "starting",
  working: "working",
  waiting: "waiting",
  blocked: "blocked",
  idle: "idle",
  error: "error",
  unknown: "unknown",
};

/** States that want the human — drives the "needs attention" lens + badge accent. */
export const ATTENTION_STATES: ReadonlySet<AgentActivityState> = new Set([
  "waiting",
  "blocked",
  "error",
]);

export function agentStateColor(s: AgentActivityState, dark: boolean): string {
  // Fall back to the `unknown` tone for any out-of-contract value: a streamed
  // delta can carry a state the client's enum predates, and `map[bad]` is
  // `undefined` → a downstream `.field` read white-screens the whole view.
  const map = dark ? AGENT_STATE_HEX_DARK : AGENT_STATE_HEX_LIGHT;
  return map[s] ?? map.unknown;
}

export function agentStateGlyph(s: AgentActivityState): string {
  return AGENT_STATE_GLYPH[s] ?? "·";
}

export function agentStateLabel(s: AgentActivityState): string {
  return AGENT_STATE_LABEL[s] ?? s;
}
