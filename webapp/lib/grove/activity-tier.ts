import type { AgentActivityState, WorkspaceStatus } from "./types";

/**
 * The color/transparency PHILOSOPHY — the single source of truth that both
 * rendering and sorting read, so a card's look and its sort position never
 * drift apart.
 *
 * The user's requirement, encoded once here:
 *   "Sessions that are not working must be more transparent (lower opacity);
 *    waiting must be highlighted; transparency indicates the active tier."
 *
 * Three tiers:
 *   - "active"    — working (or, with no agent session, workspace status ACTIVE
 *                   / RUNNING): FULL opacity, emerald accent, animated indicator.
 *   - "attention" — waiting / blocked / error: FULL opacity, amber (wait/block)
 *                   or red (error) accent, visually HIGHLIGHTED (ring/border).
 *                   Never dimmed — attention must pop off the wall.
 *   - "dormant"   — idle / starting / offline / unknown / no-session-and-not-
 *                   active: DIMMED. Lower opacity = lower active tier.
 *
 * The CSS accent vars are reused from agent-state-tokens.ts (`--agent-*`) and
 * status-tokens.ts (`--status-*`); no new hexes are invented here.
 */
export type ActivityTier = "active" | "attention" | "dormant";

export interface TierTreatment {
  tier: ActivityTier;
  /** Tailwind opacity class — the "transparency = lower tier" cue. */
  opacityClass: string;
  /** CSS var for the tier accent (ring / border / dot color). */
  accentVar: string;
  /** How the accent is applied: a highlight ring (attention) or none. */
  treatment: "ring" | "border" | "none";
}

const TREATMENTS: Record<AgentActivityState, TierTreatment> = {
  working: { tier: "active", opacityClass: "opacity-100", accentVar: "var(--agent-working)", treatment: "none" },
  waiting: { tier: "attention", opacityClass: "opacity-100", accentVar: "var(--agent-waiting)", treatment: "ring" },
  blocked: { tier: "attention", opacityClass: "opacity-100", accentVar: "var(--agent-blocked)", treatment: "ring" },
  error: { tier: "attention", opacityClass: "opacity-100", accentVar: "var(--agent-error)", treatment: "ring" },
  // idle is "alive but quiet" — closest to active of the dormant states.
  idle: { tier: "dormant", opacityClass: "opacity-70", accentVar: "var(--agent-idle)", treatment: "none" },
  // starting / unknown — no live signal yet; dim hardest.
  starting: { tier: "dormant", opacityClass: "opacity-55", accentVar: "var(--agent-starting)", treatment: "none" },
  unknown: { tier: "dormant", opacityClass: "opacity-55", accentVar: "var(--agent-unknown)", treatment: "none" },
};

/**
 * tmux FALLBACK — derive a treatment from workspace status when there is no
 * agent session (`primaryState` null, e.g. a generic / non-Claude agent or a
 * workspace with no transcript yet). ACTIVE / RUNNING tmux activity reads as
 * active; everything else is dormant. Attention is an agent-only signal — a
 * bare workspace status can't ask for the human, so it never reaches attention.
 */
function fallbackTreatment(workspaceStatus: WorkspaceStatus): TierTreatment {
  if (workspaceStatus === "active" || workspaceStatus === "running") {
    return { tier: "active", opacityClass: "opacity-100", accentVar: "var(--status-active)", treatment: "none" };
  }
  // idle → dormant-but-near (opacity-70); offline / paused / orphaned / error
  // → dim hardest (opacity-55).
  const near = workspaceStatus === "idle";
  return {
    tier: "dormant",
    opacityClass: near ? "opacity-70" : "opacity-55",
    accentVar: `var(--status-${workspaceStatus})`,
    treatment: "none",
  };
}

/**
 * The one place the "what tier is this card" policy lives. Pass the primary
 * session's agent state, or `null` to take the tmux/status fallback.
 */
export function tierForActivity(
  primaryState: AgentActivityState | null,
  workspaceStatus: WorkspaceStatus,
): TierTreatment {
  if (primaryState == null) return fallbackTreatment(workspaceStatus);
  // `?? unknown` guards an out-of-contract state from a streamed delta — the
  // TS union doesn't constrain runtime JSON, and an undefined treatment would
  // crash on the first `.tier` / `.opacityClass` read.
  return TREATMENTS[primaryState] ?? TREATMENTS.unknown;
}

const RANK: Record<ActivityTier, number> = { active: 0, attention: 1, dormant: 2 };

/**
 * Sort key — LOWER comes first. active=0, attention=1, dormant=2, so running
 * sessions float to the front of the wall. Tie-breaking within a tier is the
 * caller's job (e.g. most-recently-observed first).
 */
export function activityRank(
  primaryState: AgentActivityState | null,
  workspaceStatus: WorkspaceStatus,
): number {
  return RANK[tierForActivity(primaryState, workspaceStatus).tier];
}

/** Convenience: is this card in the active tier? */
export function isActive(
  primaryState: AgentActivityState | null,
  workspaceStatus: WorkspaceStatus,
): boolean {
  return tierForActivity(primaryState, workspaceStatus).tier === "active";
}
