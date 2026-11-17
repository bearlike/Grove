import { agentStateOf } from "./filter";
import type { FleetRow, TaskPhase } from "./types";

/**
 * The board's columns are the agent's own phase vocabulary, plus one for
 * workspaces that have claimed nothing. "Unreported" leads because it is the
 * column a person has to go and look at; the six phases then run left to
 * right so completion reads rightward, the way every board a reader has used
 * before does.
 *
 * The phase list is read off the wire type, never restated: a seventh phase
 * becomes a type error at `PHASE_COLUMNS` rather than a card that falls off
 * the board.
 */
export const PHASE_COLUMNS: readonly TaskPhase[] = [
  "scope",
  "plan",
  "build",
  "verify",
  "deliver",
  "handoff",
];

export const UNREPORTED_COLUMN = "unreported";
export type ColumnKey = TaskPhase | typeof UNREPORTED_COLUMN;
export const COLUMN_KEYS: readonly ColumnKey[] = [UNREPORTED_COLUMN, ...PHASE_COLUMNS];

/** Which column a row belongs in — a read of its claim, never an inference. */
export function columnOf(row: FleetRow): ColumnKey {
  return row.workspace.phase?.phase ?? UNREPORTED_COLUMN;
}

export interface KanbanColumn {
  readonly key: ColumnKey;
  readonly rows: readonly FleetRow[];
}

/**
 * Arrange already-filtered, already-sorted rows into phase columns. Every
 * column exists even when empty: a board whose "Verify" column vanishes when
 * nothing is verifying reads as a different board each visit, and an empty
 * column is itself a reading ("nothing is being verified").
 */
export function kanbanColumns(rows: readonly FleetRow[]): KanbanColumn[] {
  const buckets = new Map<ColumnKey, FleetRow[]>(COLUMN_KEYS.map((key) => [key, []]));
  for (const row of rows) buckets.get(columnOf(row))!.push(row);
  return COLUMN_KEYS.map((key) => ({ key, rows: buckets.get(key)! }));
}

export interface FleetProgress {
  readonly total: number;
  /** Rows claiming `handoff` and not blocked — the one state that is finished. */
  readonly done: number;
  /** Rows claiming any phase other than handoff. */
  readonly inFlight: number;
  readonly unreported: number;
  readonly blocked: number;
  readonly attention: number;
  /** `done / total`, or null for an empty board — a fraction of nothing is not 0%. */
  readonly fraction: number | null;
  /** Row counts per column, in `COLUMN_KEYS` order, for the segmented bar. */
  readonly byColumn: readonly { readonly key: ColumnKey; readonly count: number }[];
}

/**
 * The figures the header strip prints. `done` is handoff-and-not-blocked on
 * purpose: a blocked handoff is a claim that could not be finished, and
 * counting it as complete is the direction that misleads.
 */
export function fleetProgress(rows: readonly FleetRow[]): FleetProgress {
  const columns = kanbanColumns(rows);
  let done = 0;
  let blocked = 0;
  let attention = 0;
  for (const row of rows) {
    const phase = row.workspace.phase;
    if (phase?.blocked) blocked += 1;
    else if (phase?.phase === "handoff") done += 1;
    if (row.workspace.needs_attention) attention += 1;
  }
  const unreported = columns[0]!.rows.length;
  return {
    total: rows.length,
    done,
    inFlight: rows.length - unreported - done,
    unreported,
    blocked,
    attention,
    fraction: rows.length === 0 ? null : done / rows.length,
    byColumn: columns.map(({ key, rows: held }) => ({ key, count: held.length })),
  };
}

/**
 * Selection is a set of workspace ids over the VISIBLE rows. Every operation
 * takes the visible list as an argument rather than closing over it, so a
 * filter change narrows what "all" means without a stale set lingering
 * behind the toolbar's count.
 */
export type Selection = ReadonlySet<string>;

export const NO_SELECTION: Selection = new Set();

const idOf = (row: FleetRow): string => row.workspace.state.id;

export function selectAll(visible: readonly FleetRow[]): Selection {
  return new Set(visible.map(idOf));
}

/** The visible rows NOT currently selected — the "select non-like" operation. */
export function invertSelection(visible: readonly FleetRow[], selected: Selection): Selection {
  return new Set(visible.map(idOf).filter((id) => !selected.has(id)));
}

export function toggleSelected(selected: Selection, id: string): Selection {
  const next = new Set(selected);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  return next;
}

/** Drop ids that are no longer on screen, so a filter change cannot act on hidden rows. */
export function pruneSelection(selected: Selection, visible: readonly FleetRow[]): Selection {
  const onScreen = new Set(visible.map(idOf));
  const kept = [...selected].filter((id) => onScreen.has(id));
  return kept.length === selected.size ? selected : new Set(kept);
}

/**
 * Which lifecycle verbs the selected rows can take, decided per row the same
 * way the single-workspace controls decide: pause needs a live workspace,
 * resume a paused one, kill anything. A verb is offered when ANY selected row
 * can take it and applied only to the rows that can, so a mixed selection
 * never 409s on the rows that could not.
 */
export interface BulkTargets {
  readonly pause: readonly string[];
  readonly resume: readonly string[];
  readonly kill: readonly string[];
}

export function bulkTargets(visible: readonly FleetRow[], selected: Selection): BulkTargets {
  const rows = visible.filter((row) => selected.has(idOf(row)));
  const live = (row: FleetRow): boolean =>
    ["running", "active", "idle", "provisioning"].includes(row.workspace.state.status);
  return {
    pause: rows.filter(live).map(idOf),
    resume: rows.filter((row) => row.workspace.state.status === "paused").map(idOf),
    kill: rows.map(idOf),
  };
}

/** How many of the selected rows are still working — the number a kill confirmation states. */
export function workingCount(visible: readonly FleetRow[], selected: Selection): number {
  return visible.filter((row) => selected.has(idOf(row)) && agentStateOf(row.workspace) === "working")
    .length;
}
