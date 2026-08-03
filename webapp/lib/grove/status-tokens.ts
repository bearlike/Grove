import type { WorkspaceStatus, TaskPhase } from "./types";

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
  // Aliased to IDLE's info cyan in Python (`_DARK_PROVISIONING = _DARK_IDLE`) —
  // alive but not yet ready. Deliberately NOT the muted gray it used to read as.
  provisioning: "#c2dcf7",
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
  // Mirrors the Python alias: whatever IDLE is on this theme, PROVISIONING is.
  provisioning: "#2563eb",
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
  // A dotted ring — the same circle family as idle/offline but not yet drawn,
  // so a grayscale reader sees "being assembled" without reading the hue.
  provisioning: "◌",
};

export const STATUS_LABEL: Record<WorkspaceStatus, string> = {
  active: "active",
  running: "active", // RUNNING intent rarely seen post-reconciliation
  idle: "idle",
  offline: "offline",
  paused: "paused",
  orphaned: "orphaned",
  error: "error",
  provisioning: "provisioning",
};

export function statusColor(s: WorkspaceStatus, dark: boolean): string {
  // Fall back to the neutral `offline` tone for any out-of-contract value —
  // never return `undefined` (a `.field` read on it crashes the render).
  const map = dark ? STATUS_HEX_DARK : STATUS_HEX_LIGHT;
  return map[s] ?? map.offline;
}

export function statusGlyph(s: WorkspaceStatus): string {
  return STATUS_GLYPH[s] ?? "?";
}

export function statusLabel(s: WorkspaceStatus): string {
  return STATUS_LABEL[s] ?? s;
}

/**
 * Task-phase hex — dark mode. Mirrors grove.core.contracts.phase_palette.
 * Drift caught by tests/unit/status-tokens.test.ts. UNLIKE the two axes
 * above, this is a SEQUENTIAL ramp, not six independent categorical hues —
 * see phase_palette.py's module docstring. `done` deliberately leaves the
 * ramp for the shared neutral-gray atom the moment a task converges, the
 * same way `offline`/`idle` recede on their own axes.
 */
export const PHASE_HEX_DARK: Record<TaskPhase, string> = {
  scoping: "#e4f7c0",
  planning: "#cbeb8a",
  implementing: "#a8dd47",
  verifying: "#84cc16",
  delivering: "#5f9c0f",
  done: "#96938c",
};

/**
 * Task-phase hex — light mode. Client-tuned like STATUS_HEX_LIGHT /
 * AGENT_STATE_HEX_LIGHT (there is no Python light palette to mirror): each
 * dark member is shifted ~2 tailwind-lime steps darker for legibility on a
 * light canvas — the same magnitude STATUS_HEX_LIGHT.active already uses
 * (lime-500 dark → lime-700 light). `verifying` lands on the IDENTICAL hex
 * as `STATUS_HEX_LIGHT.active` (#4d7c0f) because both ARE the brand-lime
 * peak on their own axis. `done` reuses the shared neutral-gray light atom
 * (matches offline/paused/idle) so a finished task recedes the same way a
 * finished workspace does.
 */
export const PHASE_HEX_LIGHT: Record<TaskPhase, string> = {
  scoping: "#a3e635",
  planning: "#84cc16",
  implementing: "#65a30d",
  verifying: "#4d7c0f",
  delivering: "#3f6212",
  done: "#52525b",
};

/**
 * One glyph per phase — mirrors the AGENT_STATE_GLYPH/STATUS_GLYPH idiom,
 * but chosen to encode PROGRESS in the shape itself (a fill stage), not just
 * identity, so a colorblind/grayscale reader still sees advancement. `done`
 * breaks the fill sequence for a check mark, matching the palette's own
 * "done leaves the ramp" rule.
 */
export const PHASE_GLYPH: Record<TaskPhase, string> = {
  scoping: "○",
  planning: "◔",
  implementing: "◑",
  verifying: "◕",
  delivering: "●",
  done: "✓",
};

export const PHASE_LABEL: Record<TaskPhase, string> = {
  scoping: "scoping",
  planning: "planning",
  implementing: "implementing",
  verifying: "verifying",
  delivering: "delivering",
  done: "done",
};

export function phaseColor(p: TaskPhase, dark: boolean): string {
  const map = dark ? PHASE_HEX_DARK : PHASE_HEX_LIGHT;
  return map[p] ?? map.scoping;
}

export function phaseGlyph(p: TaskPhase): string {
  return PHASE_GLYPH[p] ?? "○";
}

export function phaseLabel(p: TaskPhase): string {
  return PHASE_LABEL[p] ?? p;
}

/**
 * Coarse pull-request lifecycle bucket, derived from `TicketRef.status`
 * (freeform upstream text — Story P1 says "status carries the real state,
 * including MERGED, distinct from closed" but not the exact casing/wording,
 * since it lands concurrently). Matched case-insensitively by substring so a
 * `"MERGED"` / `"Merged"` / `"merged"` from any provider all land the same
 * bucket; unrecognized text falls to `open` — a PR should read as "look at
 * this" by default, never silently vanish for an unmapped status string.
 */
export type PrState = "open" | "merged" | "closed";

export function prState(status: string | null | undefined): PrState {
  const s = (status ?? "").toLowerCase();
  if (s.includes("merged")) return "merged";
  if (s.includes("closed")) return "closed";
  return "open";
}

/**
 * PR state → CSS var, reusing tokens already on the card (no new hue, per
 * design-system.md's one-accent + semantic-palette rules):
 *   - `merged` → `--phase-done`, the SAME muted gray `PhaseBadge` already
 *     uses once a task's phase leaves the ramp — a merged PR is exactly as
 *     terminal/settled as a finished task, so it recedes the same way.
 *   - `open`   → `--ref-info` (blue), the existing "here's a pointer, in
 *     flight" hue (already worn by the agent identifier / idle status).
 *   - `closed` (unmerged) → `--status-error` (red), the same hue that
 *     already marks a broken lifecycle elsewhere on the card — the work was
 *     discarded, not delivered.
 */
export function prStateVar(state: PrState): string {
  switch (state) {
    case "merged":
      return "var(--phase-done)";
    case "closed":
      return "var(--status-error)";
    default:
      return "var(--ref-info)";
  }
}

export const PR_STATE_LABEL: Record<PrState, string> = {
  open: "open",
  merged: "merged",
  closed: "closed",
};

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
