import { facetCounts } from "@/components/grove/facets";
import type { DashboardSnapshotView } from "@/lib/grove/api";

import type { AgentState, FleetRow, WorkspaceActivity } from "./types";

/**
 * The fleet's query/filter state. Criteria are additive; `groupBy` arranges
 * surviving rows without admitting or hiding any.
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
  /** How the surviving rows are arranged. */
  readonly groupBy: "none" | "project";
  /** Board only: fold an empty phase column to a rail so full ones get the width. */
  readonly foldEmpty: boolean;
  /**
   * Show every person's workspaces, not only the signed-in caller's. Off by
   * default: the fleet opens on what you created. Narrows a view and grants
   * nothing, since everyone may read every workspace.
   */
  readonly everyone: boolean;
}

export const NO_FILTER: FleetFilter = {
  query: "",
  attentionOnly: false,
  hiddenStates: [],
  hiddenProjects: [],
  groupBy: "none",
  foldEmpty: true,
  everyone: false,
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
 * How coarsely recency RANKS a row. Rows whose activity falls in the same
 * minute hold a stable order relative to each other.
 *
 * This is the whole answer to "the rail reorders constantly with only one or
 * two sessions active", and the reason is structural rather than a bug in the
 * comparator: **sorting by a value that advances continuously produces a list
 * that reorders continuously.** `lastActivityAt` moves every time an agent
 * emits anything, so at full precision two busy workspaces trade places on
 * every SSE delta — several times a second — and the row a person was about to
 * click moves out from under them. A navigation rail exists to let somebody
 * FIND a workspace, and constant re-ranking defeats exactly that.
 *
 * A minute is chosen because it is well below the threshold at which "what
 * moved most recently" stops being true at a glance, and far above the SSE
 * cadence that was causing the churn. Note the deliberate consequence: within
 * one bucket the top row is not necessarily the very newest — it is the
 * lowest id. That trade is the point.
 *
 * It does NOT change what the row DISPLAYS. The rail still renders the precise
 * last-update time from the same transcript stamps, so the guide's rule that a
 * list sorted by a value must show that value still holds; only the ranking is
 * quantized.
 */
const RANK_BUCKET_MS = 60_000;
const rankByWorkspace = new WeakMap<WorkspaceActivity, number>();

/** Lifecycle, not agent silence: an idle session may still be a live workspace. */
export function isInactive(workspace: WorkspaceActivity): boolean {
  return ["offline", "orphaned", "paused"].includes(workspace.state.status);
}

/**
 * The whole fleet as ONE list, newest activity first.
 *
 * Flat rather than grouped by project because a Grove fleet is not read by
 * repo: a workspace is often empty, often momentary, and the question a person
 * actually asks is "what moved most recently".
 *
 * TOTAL by construction, and both halves earn their place. The bucket above
 * stops a live agent from re-ranking the list on every event. The workspace-id
 * tiebreaker under it stops rows that have NOT moved from shuffling when the
 * snapshot's own source order changes — the daemon builds it from set- and
 * dict-derived scans, so its order is not contractually stable, and
 * `Array.prototype.sort` being stable means equal keys simply inherit whatever
 * order arrived. Without the tiebreaker the rail is only as steady as an
 * ordering nobody promised.
 *
 * Age, phase and attention deliberately do NOT rank. Age is how long a
 * workspace has existed, which says nothing about where a reader should look
 * next. Attention is saturated on a real host (16 of 17 rows, because every
 * finished turn reads `waiting`), so ranking by it would sort on noise. Both
 * stay visible as marks on the row instead.
 */
export function sortedFleetRows(snapshot: DashboardSnapshotView | undefined): FleetRow[] {
  // Unchanged immutable rows keep their rank across stream deltas.
  return fleetRows(snapshot).map((row) => {
    let rank = rankByWorkspace.get(row.workspace);
    if (rank === undefined) {
      rank = Math.floor(lastActivityAt(row.workspace) / RANK_BUCKET_MS);
      rankByWorkspace.set(row.workspace, rank);
    }
    return { row, rank };
  }).sort((a, b) => {
    const inactive = Number(isInactive(a.row.workspace)) - Number(isInactive(b.row.workspace));
    if (inactive !== 0) return inactive;
    const recency = b.rank - a.rank;
    if (recency !== 0) return recency;
    return a.row.workspace.state.id < b.row.workspace.state.id
      ? -1
      : a.row.workspace.state.id > b.row.workspace.state.id
        ? 1
        : 0;
  }).map(({ row }) => row);
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

/**
 * Whether one row survives the filter. `me` is the caller's subject; until it
 * is known nothing is narrowed by creator, so the fleet never flashes empty.
 * A workspace with no recorded creator was made by this host's own user.
 */
export function admits(row: FleetRow, filter: FleetFilter, me?: string): boolean {
  if (!filter.everyone && me !== undefined && (row.workspace.state.created_by ?? "local") !== me) {
    return false;
  }
  if (filter.attentionOnly && !row.workspace.needs_attention) return false;
  if (filter.hiddenStates.includes(agentStateOf(row.workspace))) return false;
  if (filter.hiddenProjects.includes(row.repoRoot)) return false;
  return matchesQuery(row, filter.query);
}

export function filterRows(
  rows: readonly FleetRow[],
  filter: FleetFilter,
  me?: string,
): FleetRow[] {
  return rows.filter((row) => admits(row, filter, me));
}

export interface FleetGroup {
  readonly key: string;
  readonly repoName: string | null;
  readonly rows: FleetRow[];
}

/**
 * Arrange already-filtered fleet rows without changing their recency order.
 *
 * Projects can arrive as several nested groups with one `repo_root`; grouping
 * by that root keeps them together while a project-name collision remains two
 * distinct groups.
 *
 * Groups take the order of their FIRST row, i.e. the project whose newest
 * workspace moved most recently leads. They were alphabetical, and the rail
 * groups by default, so a workspace that had just replied could sit below a
 * stale one in a project whose name sorts first — the reported "most recent
 * is not at the top" (#919). Map insertion order over sorted rows is that
 * order already.
 */
export function fleetGroups(
  rows: readonly FleetRow[],
  groupBy: FleetFilter["groupBy"],
): FleetGroup[] {
  if (rows.length === 0) return [];
  if (groupBy === "none") return [{ key: "all", repoName: null, rows: [...rows] }];

  const groups = new Map<string, { key: string; repoName: string; rows: FleetRow[] }>();
  for (const row of rows) {
    const group = groups.get(row.repoRoot);
    if (group) group.rows.push(row);
    else groups.set(row.repoRoot, { key: row.repoRoot, repoName: row.repoName, rows: [row] });
  }

  return [...groups.values()];
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
