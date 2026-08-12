import { facetCounts } from "@/components/grove/facets";
import type { DashboardSnapshotView } from "@/lib/grove/api";

import type { AgentState, FleetRow, WorkspaceActivity } from "./types";

/**
 * How the fleet is narrowed. Every criterion is additive.
 *
 * The two set-valued criteria are HIDE sets, mirroring `webapp/`'s
 * `DashboardFilterState`: an empty filter shows everything, AND a state or a
 * repo that streams in *later* is visible by default. A SHOW set would freeze
 * the menu's contents at the moment it was first opened and silently hide the
 * next workspace in a repo the user has never seen.
 */
export interface FleetFilter {
  readonly query: string;
  readonly attentionOnly: boolean;
  /** Agent states to hide. */
  readonly hiddenStates: readonly AgentState[];
  /** `repo_root`s to hide. */
  readonly hiddenProjects: readonly string[];
}

export const NO_FILTER: FleetFilter = {
  query: "",
  attentionOnly: false,
  hiddenStates: [],
  hiddenProjects: [],
};

/**
 * A workspace with no session has no agent to report on, which is a different
 * thing from an agent that reports nothing — `unknown` says so.
 */
export function agentStateOf(workspace: WorkspaceActivity): AgentState {
  return workspace.sessions[0]?.activity.state ?? "unknown";
}

export function currentTaskOf(workspace: WorkspaceActivity): string | null {
  return workspace.sessions[0]?.activity.current_task ?? null;
}

// `lastActivityIso` / `lastActivityAt` MOVED to `lib/grove/adapters/activity.ts`.
// A third surface needs them — the workspace Info tab, whose Timeline card was
// showing the frozen `state.updated_at` and reporting a live session as four
// hours idle — and "what a wire shape derives to" is the adapters' job, not
// the fleet filter's. Re-exported here so the rail and the dashboard keep
// importing their one filter vocabulary from one place.
export { lastActivityAt, lastActivityIso } from "@/lib/grove/adapters";
// Imported as well as re-exported: the sort below is a local caller.
import { lastActivityAt } from "@/lib/grove/adapters";

/** Every workspace in the fleet, flattened so it can be searched across projects. */
export function fleetRows(snapshot: DashboardSnapshotView | undefined): FleetRow[] {
  return (snapshot?.projects ?? []).flatMap((project) =>
    project.workspaces.map((workspace) => ({
      workspace,
      repoName: project.repo_name,
      repoRoot: project.repo_root,
    })),
  );
}

/**
 * The whole fleet as ONE list, newest activity first.
 *
 * Flat rather than grouped by project because a Grove fleet is not read by
 * repo: a workspace is often empty, often momentary, and the question a person
 * actually asks is "what moved most recently". `Array.sort` is stable, so rows
 * with identical recency keep the engine's own ordering.
 */
export function sortedFleetRows(snapshot: DashboardSnapshotView | undefined): FleetRow[] {
  return fleetRows(snapshot).sort(
    (a, b) => lastActivityAt(b.workspace) - lastActivityAt(a.workspace),
  );
}

/**
 * Matching is over everything a person would plausibly type to find a
 * workspace: its title, its branch, its repo, its agent, and the ticket ids
 * attached to it.
 */
export function matchesQuery(row: FleetRow, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (needle === "") return true;
  const { state } = row.workspace;
  const haystack = [
    state.title,
    state.branch,
    state.agent_name,
    row.repoName,
    ...state.ticket_refs.map((ticket) => `${ticket.provider}#${ticket.id}`),
  ];
  return haystack.some((value) => value.toLowerCase().includes(needle));
}

/** Whether one row survives the filter. */
export function admits(row: FleetRow, filter: FleetFilter): boolean {
  if (filter.attentionOnly && !row.workspace.needs_attention) return false;
  if (filter.hiddenStates.includes(agentStateOf(row.workspace))) return false;
  if (filter.hiddenProjects.includes(row.repoRoot)) return false;
  return matchesQuery(row, filter.query);
}

export function filterRows(rows: readonly FleetRow[], filter: FleetFilter): FleetRow[] {
  return rows.filter((row) => admits(row, filter));
}

/** How many criteria are doing work — the number the filter button badges. */
export function activeFilterCount(filter: FleetFilter): number {
  return (
    (filter.attentionOnly ? 1 : 0) + filter.hiddenStates.length + filter.hiddenProjects.length
  );
}

/** The order the filter menu lists agent states in — most active first. */
const STATE_ORDER: readonly AgentState[] = [
  "working",
  "waiting",
  "blocked",
  "starting",
  "idle",
  "error",
  "unknown",
];

export interface FleetFacets {
  readonly states: readonly { readonly state: AgentState; readonly count: number }[];
  readonly projects: readonly {
    readonly repoRoot: string;
    readonly repoName: string;
    readonly count: number;
  }[];
  readonly attention: number;
}

/**
 * What the filter menu offers, with live counts, derived from the UNFILTERED
 * rows. Counting the filtered set instead would make every row read `0` the
 * moment it was hidden, and there would be no way to tell an empty category
 * from one you had just switched off.
 *
 * Nested project groups share a `repo_root` and differ only by `cwd`, so they
 * collapse into one row here — otherwise one repo appears N times in the menu.
 */
export function fleetFacets(rows: readonly FleetRow[]): FleetFacets {
  // The counting itself is `facets.ts`'s job — this function is the fleet's
  // POLICY (which dimensions, in what order), and the session browser states a
  // different policy over the same mechanism.
  const states = facetCounts(rows, (row) => {
    const state = agentStateOf(row.workspace);
    return { id: state, label: state };
  });

  return {
    states: states
      .map(({ id, count }) => ({ state: id, count }))
      .sort((a, b) => STATE_ORDER.indexOf(a.state) - STATE_ORDER.indexOf(b.state)),
    // Nested project groups share a `repo_root` and differ only by `cwd`, so
    // keying on the root is what collapses one repo's several groups into one
    // menu row.
    projects: facetCounts(rows, (row) => ({ id: row.repoRoot, label: row.repoName })).map(
      ({ id, label, count }) => ({ repoRoot: id, repoName: label, count }),
    ),
    attention: rows.filter((row) => row.workspace.needs_attention).length,
  };
}

/**
 * How the wall states its size. Split out because it is easy to get wrong in a
 * template literal and impossible to notice afterwards — "1 workspaces" shipped
 * that way.
 */
export function workspaceCountLabel(shown: number, total: number): string {
  const noun = total === 1 ? "workspace" : "workspaces";
  return shown === total ? `${total} ${noun}` : `${shown} of ${total} ${noun}`;
}
