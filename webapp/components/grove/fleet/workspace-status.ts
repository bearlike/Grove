import { phaseLabel } from "./tokens";
import type { WorkspaceActivity } from "./types";

/** A Grove report, never the latest human prompt masquerading as current work. */
export function workspaceStatusText({ phase }: Pick<WorkspaceActivity, "phase">): string | null {
  if (!phase) return null;
  const text = phase.note?.trim() || phaseLabel(phase.phase);
  return phase.blocked ? `Blocked — ${text}` : text;
}
