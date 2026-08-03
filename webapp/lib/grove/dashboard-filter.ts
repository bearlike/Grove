import type {
  AgentActivityState,
  DashboardSnapshotView,
  WorkspaceActivityView,
} from "./types";
import { activityRank } from "./activity-tier";

// Wall-presentation policy: which workspaces show, in what order, under what
// repo section. Pure functions over a snapshot — the stream contract + reducer
// that PRODUCE the snapshot live in `activity-stream.ts`; keep the two concerns
// apart so transport changes never touch presentation policy and vice versa.
//
// The view *intent* (scope / hidden states / attention-only / query) lives in
// the Zustand `ui-store`; this module is the pure selector that turns that
// intent + the server snapshot into the repo-grouped grid the page renders.

// ─── Facets (sidebar input — FROZEN signature) ───────────────────────────────
//
// The sidebar agent imports `computeFacets`; its return shape is a contract.
// Project + agent-state distribution of the whole snapshot, with live counts.

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

/** Project + agent-state distribution of the whole snapshot — feeds the sidebar (live counts). */
export function computeFacets(snapshot: DashboardSnapshotView): DashboardFacets {
  // Nested-project groups share a `repo_root` but differ in `cwd`; the
  // rail's per-repo sessions source (`GET /sessions?repo=`) has no per-subpath
  // scoping, so a facet-level section is per-REPO, not per-group — collapse by
  // `repo_root` here (first occurrence's `repo_name` wins, counts sum) so a
  // repo with N nested cwds renders one rail section instead of N duplicate-
  // keyed ones each listing the whole repo.
  const byRoot = new Map<string, { repo_root: string; repo_name: string; count: number }>();
  for (const g of snapshot.projects) {
    const existing = byRoot.get(g.repo_root);
    if (existing) {
      existing.count += g.workspaces.length;
    } else {
      byRoot.set(g.repo_root, {
        repo_root: g.repo_root,
        repo_name: g.repo_name,
        count: g.workspaces.length,
      });
    }
  }
  const projects = [...byRoot.values()];
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

// ─── Attention-first ordering ────────────────────────────────────────────────
//
// A section is glanceable only if what NEEDS YOU sits at its top. The rank
// itself lives with the tier policy (`activityRank` in activity-tier.ts:
// attention=0 < active=1 < dormant=2, with the tmux fallback for session-less
// workspaces) so a card's sort position and its dim/highlight treatment can
// never drift apart.

function workspaceRank(w: WorkspaceActivityView): number {
  // null primary → activityRank takes the tmux/workspace-status fallback.
  return activityRank(w.sessions[0]?.activity.state ?? null, w.state.status);
}

/**
 * The attention-first comparator: lower `activityRank` first (action-required,
 * then working, then dormant), tie-broken by `observed_at` desc (freshest
 * first). Returns 0 for fully-equal keys so `Array.sort` (stable) preserves
 * incoming order and the SSE-driven reorder stays deterministic frame to frame.
 */
export function compareByAttention(
  a: WorkspaceActivityView,
  b: WorkspaceActivityView,
): number {
  const byRank = workspaceRank(a) - workspaceRank(b);
  if (byRank !== 0) return byRank;
  return b.observed_at.localeCompare(a.observed_at);
}

// ─── The grid selector (store intent → repo-grouped sections) ────────────────
//
// The single selector the grid consumes. View intent is the set of things to
// HIDE plus an optional repo scope and a free-text query, so the empty default
// shows everything AND a state/repo that appears later is visible by default.

/** The view intent the grid filters by — the relevant slice of the ui-store. */
export interface GridView {
  /** Repo scope: null = all repos, else the `repo_root` to narrow to. */
  scopeRepo: string | null;
  /** Agent-states to HIDE (empty = show all). */
  hiddenStates: readonly AgentActivityState[];
  /** Keep only workspaces that want the human. */
  attentionOnly: boolean;
  /** Free-text match over title + branch (case-insensitive; empty = no filter). */
  query: string;
}

/** One repo section of the grid: the repo identity + its (filtered, sorted) cards. */
export interface RepoSection {
  repo_root: string;
  repo_name: string;
  workspaces: WorkspaceActivityView[];
}

function matchesQuery(w: WorkspaceActivityView, needle: string): boolean {
  if (needle === "") return true;
  const q = needle.toLowerCase();
  return (
    w.state.title.toLowerCase().includes(q) || w.state.branch.toLowerCase().includes(q)
  );
}

/**
 * Turn the server snapshot + the store's view intent into repo-grouped sections,
 * in snapshot order (the engine sorts roots), each section attention-first.
 * Empty sections are dropped; with a `scopeRepo` set, only that repo survives.
 * Pure — the grid renders exactly what this returns, the page owns no policy.
 */
export function selectSections(
  snapshot: DashboardSnapshotView,
  view: GridView,
): RepoSection[] {
  const hidden = new Set(view.hiddenStates);
  const query = view.query.trim();
  return snapshot.projects
    .filter((g) => view.scopeRepo == null || g.repo_root === view.scopeRepo)
    .map((g) => ({
      repo_root: g.repo_root,
      repo_name: g.repo_name,
      workspaces: g.workspaces
        .filter(
          (w) =>
            !hidden.has(displayState(w)) &&
            (!view.attentionOnly || w.needs_attention) &&
            matchesQuery(w, query),
        )
        .sort(compareByAttention),
    }))
    .filter((s) => s.workspaces.length > 0);
}
