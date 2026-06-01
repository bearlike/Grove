import type { WorkspaceStatus } from "./types";

/**
 * Status hex — dark mode. Mirrors grove.core.contracts.status_palette.
 * Drift caught by tests/unit/status-tokens.test.ts.
 */
export const STATUS_HEX_DARK: Record<WorkspaceStatus, string> = {
  active: "#84cc16",
  running: "#84cc16",
  idle: "#c2dcf7",
  offline: "#96938c",
  paused: "#96938c",
  orphaned: "#b8860b",
  error: "#e64c4c",
};

/**
 * Status hex — light mode. Tuned for browser white/slate surfaces (the
 * TUI's cream-and-tan-brown light values are inappropriate here). Same
 * intent — lime / blue / amber / red — different concrete values.
 */
export const STATUS_HEX_LIGHT: Record<WorkspaceStatus, string> = {
  active: "#65a30d",
  running: "#65a30d",
  idle: "#2563eb",
  offline: "#71717a",
  paused: "#71717a",
  orphaned: "#b45309",
  error: "#b91c1c",
};

/** Active-pulse swelled hex (mirrors grove.tui.theme._*_STATUS_ACTIVE_TINT). */
export const STATUS_ACTIVE_TINT_DARK = "#bef264";
export const STATUS_ACTIVE_TINT_LIGHT = "#84cc16";

export const STATUS_GLYPH: Record<WorkspaceStatus, string> = {
  active: "●",
  running: "●",
  idle: "◐",
  offline: "○",
  paused: "‖",
  orphaned: "⊘",
  error: "✗",
};

export const STATUS_LABEL: Record<WorkspaceStatus, string> = {
  active: "active",
  running: "active", // RUNNING intent rarely seen post-reconciliation
  idle: "idle",
  offline: "offline",
  paused: "paused",
  orphaned: "orphaned",
  error: "error",
};

export function statusColor(s: WorkspaceStatus, dark: boolean): string {
  return (dark ? STATUS_HEX_DARK : STATUS_HEX_LIGHT)[s];
}

export function statusGlyph(s: WorkspaceStatus): string {
  return STATUS_GLYPH[s] ?? "?";
}

export function statusLabel(s: WorkspaceStatus): string {
  return STATUS_LABEL[s] ?? s;
}

/**
 * Polarity-aware stat color. Mirrors `_stat()` in src/grove/tui/screens/list.py:
 *   - zero → muted
 *   - ahead nonzero → ref-add (green)
 *   - behind nonzero → orphaned amber (work to pull)
 *   - dirty nonzero → orphaned amber (work to clean)
 */
export function statColor(
  kind: "ahead" | "behind" | "dirty",
  value: number,
  dark: boolean,
): string {
  if (value === 0) return dark ? "#96938c" : "#71717a";
  if (kind === "ahead") return dark ? "#99d199" : "#3d7a00";
  return dark ? "#b8860b" : "#b45309";
}
