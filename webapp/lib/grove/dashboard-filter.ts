import type {
  AgentActivityState,
  DashboardSnapshotView,
  ProjectGroupView,
  WorkspaceActivityView,
} from "./types";
import { ATTENTION_STATES } from "./agent-state-tokens";
import { activityRank } from "./activity-tier";

// Wall-presentation policy: which workspaces show, in what order, under what
// label. Pure functions over a snapshot — the stream contract + reducer that
// PRODUCE the snapshot live in `activity-stream.ts`; keep the two concerns
// apart so transport changes never touch presentation policy and vice versa.

export type Lens = "all" | "attention" | "active";

/** Lens cycle order. "all" is first: the dashboard's job is to show everything. */
export const LENSES: readonly Lens[] = ["all", "attention", "active"] as const;

export const LENS_LABEL: Record<Lens, string> = {
  all: "All",
  attention: "Needs attention",
  active: "Active",
};

const ACTIVE_LENS_STATES = new Set(["starting", "working", "waiting", "blocked"]);

/** Does one workspace pass the given lens? Mirrors the TUI's `_passes_lens`. */
export function passesLens(w: WorkspaceActivityView, lens: Lens): boolean {
  if (lens === "all") return true;
  if (lens === "attention") return w.needs_attention;
  const primary = w.sessions[0]?.activity;
  return primary != null && ACTIVE_LENS_STATES.has(primary.state);
}

/** Project groups with each group's workspaces filtered by the lens; empties dropped. */
export function groupsForLens(
  snapshot: DashboardSnapshotView,
  lens: Lens,
): ProjectGroupView[] {
  return snapshot.projects
    .map((g) => ({ ...g, workspaces: g.workspaces.filter((w) => passesLens(w, lens)) }))
    .filter((g) => g.workspaces.length > 0);
}

/** True when an activity wants the human — used by the card accent. */
export function wantsAttention(state: string): boolean {
  return ATTENTION_STATES.has(state as never);
}

// ─── Consolidated dashboard filter (projects × agent-states × attention) ─────
//
// One filter replaces the three lens tabs. Stored as the set of things to HIDE,
// so the empty default shows everything AND a state/project that appears later
// is visible by default — a deselected item is an explicit choice, never a stale
// snapshot of "what existed when I opened the menu".

export interface DashboardFilterState {
  hiddenProjects: ReadonlySet<string>; // repo_root
  hiddenStates: ReadonlySet<AgentActivityState>;
  attentionOnly: boolean;
}

export function emptyFilter(): DashboardFilterState {
  return { hiddenProjects: new Set(), hiddenStates: new Set(), attentionOnly: false };
}

export function activeFilterCount(f: DashboardFilterState): number {
  return f.hiddenProjects.size + f.hiddenStates.size + (f.attentionOnly ? 1 : 0);
}

/** The single agent-state a workspace filters/labels by (no session → "unknown"). */
export function displayState(w: WorkspaceActivityView): AgentActivityState {
  return (w.sessions[0]?.activity.state ?? "unknown") as AgentActivityState;
}

// Visual order for the state rows (most-active first).
const STATE_ORDER: AgentActivityState[] = [
  "working",
  "waiting",
  "blocked",
  "starting",
  "idle",
  "error",
  "unknown",
];

export interface DashboardFacets {
  projects: { repo_root: string; repo_name: string; count: number }[];
  states: { state: AgentActivityState; count: number }[];
  attention: number;
  total: number;
}

/** Project + agent-state distribution of the whole snapshot — feeds the filter menu (live counts). */
export function computeFacets(snapshot: DashboardSnapshotView): DashboardFacets {
  const projects = snapshot.projects.map((g) => ({
    repo_root: g.repo_root,
    repo_name: g.repo_name,
    count: g.workspaces.length,
  }));
  const stateCounts = new Map<AgentActivityState, number>();
  let attention = 0;
  let total = 0;
  for (const g of snapshot.projects) {
    for (const w of g.workspaces) {
      total += 1;
      if (w.needs_attention) attention += 1;
      const s = displayState(w);
      stateCounts.set(s, (stateCounts.get(s) ?? 0) + 1);
    }
  }
  const states = [...stateCounts.entries()]
    .map(([state, count]) => ({ state, count }))
    .sort((a, b) => STATE_ORDER.indexOf(a.state) - STATE_ORDER.indexOf(b.state));
  return { projects, states, attention, total };
}

/** Apply the filter to a snapshot → project groups (empties dropped). */
export function filterSnapshot(
  snapshot: DashboardSnapshotView,
  f: DashboardFilterState,
): ProjectGroupView[] {
  return snapshot.projects
    .filter((g) => !f.hiddenProjects.has(g.repo_root))
    .map((g) => ({
      ...g,
      workspaces: g.workspaces.filter(
        (w) => !f.hiddenStates.has(displayState(w)) && (!f.attentionOnly || w.needs_attention),
      ),
    }))
    .filter((g) => g.workspaces.length > 0);
}

// ─── Active-to-front sort ────────────────────────────────────────────────────
//
// The wall is most useful when what's *happening* sits at the top. We order on
// the shared `activityRank` policy (active=0 < attention=1 < dormant=2, with the
// tmux fallback for session-less workspaces) so a card's sort position and its
// dim/highlight treatment can never drift apart. Pure & stable so auto-animate
// at the page only ever sees a deterministic reorder.

/** The min activityRank across a group's workspaces (∞ when empty → sinks last). */
function groupRank(group: ProjectGroupView): number {
  let best = Number.POSITIVE_INFINITY;
  for (const w of group.workspaces) {
    best = Math.min(best, workspaceRank(w));
  }
  return best;
}

function workspaceRank(w: WorkspaceActivityView): number {
  // null primary → activityRank takes the tmux/workspace-status fallback.
  return activityRank(w.sessions[0]?.activity.state ?? null, w.state.status);
}

/**
 * Float active/working workspaces to the front. Within each project, sort by
 * `activityRank` ascending, tie-broken by `observed_at` desc (freshest first),
 * then by the original order. Project GROUPS are ordered so any project holding
 * an active session comes first (by each group's min rank). `Array.sort` is
 * stable, so equal keys preserve incoming order — pair this with the shared
 * comparator and the reorder stays deterministic frame to frame.
 */
export function sortSnapshotByActivity(
  groups: readonly ProjectGroupView[],
): ProjectGroupView[] {
  const sortedGroups = groups.map((g) => ({
    ...g,
    workspaces: [...g.workspaces].sort((a, b) => {
      const byRank = workspaceRank(a) - workspaceRank(b);
      if (byRank !== 0) return byRank;
      // Freshest observation first within a tier.
      const byObserved = b.observed_at.localeCompare(a.observed_at);
      if (byObserved !== 0) return byObserved;
      return 0; // equal → stable (incoming order preserved)
    }),
  }));
  return sortedGroups.sort((a, b) => groupRank(a) - groupRank(b));
}
