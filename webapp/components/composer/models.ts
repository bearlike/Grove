import type { AgentSummaryView } from "@/lib/grove/types";
import rawModels from "@/lib/grove/models.json";

/** The adapter kind — the wire discriminant that decides which launch flags exist. */
export type AgentKind = AgentSummaryView["kind"];

/** One offered model: an explicit id/alias plus a human label for the menu row. */
export interface ModelOption {
  /** The value forwarded as the agent's model arg (alias OR full id; opaque to Grove). */
  value: string;
  label: string;
}

/**
 * Default model menus keyed by adapter kind — MECHANISM only. The actual values
 * are CONFIG, declared in `lib/grove/models.json` (policy, user-editable, OUT of
 * the code), so the model lists can change with no code edit and nothing is
 * hard-coded here. This module just parses + types that file.
 *
 * A kind with a non-empty array there gets a `Model ▾` picker (claude_code +
 * codex append `--model` at launch; mewbo forwards the id to its remote
 * session-create, #98). A kind absent there (e.g. `generic`) gets no pill. The
 * picker ALWAYS also offers a free-text "Custom model id…" row, so the JSON is a
 * convenience list, never a closed set. The selected value rides the wire as
 * `CreateWorkspaceRequest.model`; null = the tool's own default. Grove only
 * forwards the opaque id — it never interprets the model (the provider boundary).
 *
 * Non-array keys in the JSON (the `$comment` doc string) are skipped.
 */
function loadModels(): Partial<Record<AgentKind, readonly ModelOption[]>> {
  const out: Partial<Record<string, readonly ModelOption[]>> = {};
  for (const [kind, value] of Object.entries(rawModels)) {
    if (Array.isArray(value)) out[kind] = value as ModelOption[];
  }
  return out as Partial<Record<AgentKind, readonly ModelOption[]>>;
}

export const MODELS_BY_KIND: Partial<Record<AgentKind, readonly ModelOption[]>> = loadModels();

/** True when the kind has any declared models (so the Model pill shows). */
export function kindSupportsModel(kind: AgentKind | undefined): boolean {
  return kind !== undefined && (MODELS_BY_KIND[kind]?.length ?? 0) > 0;
}
