import type { Runtime } from "./types";

/**
 * Runtime glyph/label/hex — the isolation axis (host vs container).
 *
 * Mirrors the cross-client contract `grove.core.contracts.runtime_palette`, the
 * same file the TUI imports. Unlike the status/agent/phase palettes, that
 * contract pins the GLYPH as well as the hex: a runtime mark is a two-member
 * vocabulary a user has to carry between the TUI and this console mid-task, so
 * the character itself is contract rather than convention. Drift in all three
 * maps is caught by tests/unit/runtime-tokens.test.ts, which reads the Python.
 *
 * Silence is NOT the signal here — deliberately unlike `PlacementBadge`. A
 * worktree-vs-root placement is an implementation detail; runtime says whether
 * the agent can reach the host filesystem, host network and the user's
 * credentials, so both states are marked. An unmarked workspace must never be
 * ambiguous between "runs on your machine" and "this component didn't render".
 */
export const RUNTIME_GLYPH: Record<Runtime, string> = {
  host: "■", // U+25A0 BLACK SQUARE — the work, no boundary drawn
  container: "▣", // U+25A3 — the same square, inside a boundary
};

export const RUNTIME_LABEL: Record<Runtime, string> = {
  host: "host",
  container: "container",
};

/** Dark-mode hex, canonical from the Python contract. */
export const RUNTIME_HEX_DARK: Record<Runtime, string> = {
  host: "#96938c", // muted gray — the ambient default
  container: "#c2dcf7", // info cyan — noteworthy, never a warning
};

/** Light-mode hex — client-tuned, the same two atoms against a light canvas. */
export const RUNTIME_HEX_LIGHT: Record<Runtime, string> = {
  host: "#71717a",
  container: "#1d4ed8",
};

/**
 * `?? host` fallback everywhere: a streamed delta can carry a runtime this
 * client's enum predates, and an unresolved lookup would render an empty mark
 * — which on this axis reads as the silence this mark exists to remove.
 */
export function runtimeGlyph(r: Runtime): string {
  return RUNTIME_GLYPH[r] ?? RUNTIME_GLYPH.host;
}

export function runtimeLabel(r: Runtime): string {
  return RUNTIME_LABEL[r] ?? r;
}

export function runtimeColor(r: Runtime, dark: boolean): string {
  const map = dark ? RUNTIME_HEX_DARK : RUNTIME_HEX_LIGHT;
  return map[r] ?? map.host;
}
