import { clamp } from "@/components/elements/range";
import { facetCounts, shows, type Facet, type HideSet } from "@/components/grove/facets";
import type { SessionSummaryView } from "@/lib/grove/api";

/**
 * How the session catalog is narrowed — the browser's POLICY over the shared
 * facet mechanism in `../facets.ts`.
 *
 * It is its own module rather than an extension of `fleet/filter.ts` because
 * the two narrow different ENTITIES: every criterion there reads
 * `row.workspace.state.*` off a live workspace, and a catalog row has no
 * workspace at all — most sessions on a host were never launched by Grove. What
 * the two genuinely share is the hide-set semantics and the counting, and that
 * is exactly what they now both import.
 *
 * WHAT A CATALOG ROW ACTUALLY CARRIES, measured against the live daemon rather
 * than read off the schema, because it decides which dimensions are worth
 * offering: `title`, `first_prompt`, `last_prompt`, `activity` and `size_bytes`
 * are null on EVERY host-scoped row (the scan is one bounded head read per
 * session and never opens the transcript). `adapter_kind`, `git_branch`, `cwd`,
 * `project` and the timestamps are present on effectively all of them. So the
 * facets are location, agent and branch — the three things the scan recovers.
 */

/** The dimensions a session can be hidden by. */
export interface SessionFilter {
  readonly query: string;
  /** `repo_root`s, or a bare `cwd` for a row that belongs to no repo. */
  readonly hiddenLocations: HideSet;
  /** `adapter_kind`s. */
  readonly hiddenAgents: HideSet;
  /** `git_branch` values. */
  readonly hiddenBranches: HideSet;
  /** Inclusive turn-count bounds. `null` at either end means unbounded. */
  readonly minTurns: number | null;
  readonly maxTurns: number | null;
}

export const NO_SESSION_FILTER: SessionFilter = {
  query: "",
  hiddenLocations: [],
  hiddenAgents: [],
  hiddenBranches: [],
  minTurns: null,
  maxTurns: null,
};

/**
 * How many turns a session recorded, or `null` for "not counted".
 *
 * NULL IS A REAL ANSWER AND IS NEVER ZERO. The host-wide catalog scan is
 * deliberately metadata-only, so a count is present only where the daemon's
 * incremental transcript cache already holds one. A fabricated `0` would claim
 * a session did nothing, which is the same lie the usage page refuses to tell
 * about unmeasured spend.
 *
 * Read structurally because `turn_count` is landing on `SessionSummaryView`
 * while this is being written: the narrowing here is the one place that has to
 * change when the schema regenerates, and it stays correct either way.
 */
export function turnCountOf(session: SessionSummaryView): number | null {
  const value = (session as { turn_count?: number | null }).turn_count;
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** The location a row is filed under: its repo when the scan resolved one, else
 * the directory itself. A session frequently runs outside any repo. */
export function locationOf(session: SessionSummaryView): { id: string; label: string } | null {
  if (session.project) {
    return { id: session.project.repo_root, label: session.project.repo_name };
  }
  return session.cwd ? { id: session.cwd, label: session.cwd } : null;
}

/**
 * True when a location facet is a bare DIRECTORY rather than a repo.
 *
 * `locationOf` sets `label` to the repo name for a repo and to the path itself
 * for everything else, so the two being equal is exactly "no repo resolved".
 * The rule lives here, beside the function whose shape it reads, rather than in
 * the menu — a caller guessing from the string would be guessing at this
 * invariant instead of asking about it.
 *
 * It matters because the two get DIFFERENT marks: drawing a repo glyph over
 * `/tmp/claude-1000/otelproof3` asserts something the scan never established.
 */
export function isBareLocation(facet: { id: string; label: string }): boolean {
  return facet.id === facet.label;
}

/**
 * The subdirectory a session ran in, relative to its repo root — `null` when it
 * ran at the root itself, or there is no repo to relativize against.
 *
 * WHY THE ROW NEEDS IT. Every Grove workspace is a worktree UNDER the repo, so
 * filing by repo alone makes forty rows read "Grove" and tells the reader
 * nothing about which is which. The worktree's directory name is the closest
 * thing a metadata-only scan has to a task name, and it is also what makes the
 * SEARCH legible: a query can match a path the row does not otherwise show, and
 * then the hit looks like a bug. Measured on this host — searching "codex"
 * matched a `claude_code` row whose only "codex" was in
 * `.worktrees/otel-enricher-…-and-codex-…`.
 *
 * Lexical and separator-aware (`/a/bc` is not under `/a/b`), never touching a
 * filesystem, mirroring the engine's own path relativization.
 */
export function relativeCwd(session: SessionSummaryView): string | null {
  const { cwd, project } = session;
  if (!cwd || !project) return null;
  const root = project.repo_root.replace(/\/+$/, "");
  if (cwd === root) return null;
  return cwd.startsWith(`${root}/`) ? cwd.slice(root.length + 1) : null;
}

/**
 * Matching is over what a person would plausibly type to find a session: where
 * it ran, which tool ran it, which branch, and the id itself — which is the one
 * field that is always present and the one a bug report quotes.
 */
export function matchesQuery(session: SessionSummaryView, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (needle === "") return true;
  const haystack = [
    session.session_id,
    session.adapter_kind,
    session.git_branch,
    session.workspace_title,
    locationOf(session)?.label,
    session.cwd,
  ];
  return haystack.some((value) => value?.toLowerCase().includes(needle) ?? false);
}

/**
 * Whether a turn count satisfies the bounds.
 *
 * AN ACTIVE RANGE HIDES UNCOUNTED SESSIONS, and that is a decision rather than
 * an oversight: "at least 20 turns" is a claim about a number, and a row whose
 * number is unknown cannot satisfy it. Admitting them would make the filter
 * silently meaningless; treating `null` as `0` would sort and filter a real
 * session as an empty one. The menu says how many rows this costs, so the
 * trade is visible rather than mysterious.
 */
export function withinTurnBounds(
  turns: number | null,
  minTurns: number | null,
  maxTurns: number | null,
): boolean {
  if (minTurns === null && maxTurns === null) return true;
  if (turns === null) return false;
  return (minTurns === null || turns >= minTurns) && (maxTurns === null || turns <= maxTurns);
}

export function admits(session: SessionSummaryView, filter: SessionFilter): boolean {
  const location = locationOf(session);
  if (location && !shows(filter.hiddenLocations, location.id)) return false;
  if (!shows(filter.hiddenAgents, session.adapter_kind)) return false;
  if (session.git_branch && !shows(filter.hiddenBranches, session.git_branch)) return false;
  if (!withinTurnBounds(turnCountOf(session), filter.minTurns, filter.maxTurns)) return false;
  return matchesQuery(session, filter.query);
}

export function filterSessions(
  sessions: readonly SessionSummaryView[],
  filter: SessionFilter,
): SessionSummaryView[] {
  return sessions.filter((session) => admits(session, filter));
}

/** How many criteria are doing work — the number the filter button badges. A
 * bounded range counts as ONE criterion however many ends are set. */
export function activeSessionFilterCount(filter: SessionFilter): number {
  return (
    filter.hiddenLocations.length +
    filter.hiddenAgents.length +
    filter.hiddenBranches.length +
    (filter.minTurns !== null || filter.maxTurns !== null ? 1 : 0)
  );
}

export interface SessionFacets {
  readonly locations: readonly Facet[];
  readonly agents: readonly Facet[];
  readonly branches: readonly Facet[];
  /** Rows an active turn range would hide because they carry no count. Zero
   * when nothing is uncounted, which is what lets the menu stay silent. */
  readonly uncounted: number;
  /**
   * Rows that DO carry a count. Zero is the live case today, not a corner:
   * the host-wide scan leaves `turn_count` null on every row by design — the
   * engine measured both cheap shortcuts and refused them, so a real count
   * waits on the usage projector. A range control over a dimension nothing
   * reports would hide the entire catalog on first keystroke, so the menu
   * offers it only once something is actually counted.
   */
  readonly counted: number;
}

/**
 * What the filter menu offers, from the UNFILTERED rows.
 *
 * Every dimension is sorted by count, largest first: a catalog reaches into the
 * hundreds, and the option worth reading is the one covering most of the list,
 * not the one that happens to sort first alphabetically.
 */
export function sessionFacets(sessions: readonly SessionSummaryView[]): SessionFacets {
  const byCount = (a: Facet, b: Facet): number => b.count - a.count;
  const counted = sessions.filter((session) => turnCountOf(session) !== null).length;
  return {
    locations: facetCounts(sessions, locationOf).sort(byCount),
    agents: facetCounts(sessions, (session) => ({
      id: session.adapter_kind,
      label: session.adapter_kind,
    })).sort(byCount),
    branches: facetCounts(sessions, (session) =>
      session.git_branch ? { id: session.git_branch, label: session.git_branch } : null,
    ).sort(byCount),
    uncounted: sessions.length - counted,
    counted,
  };
}

/**
 * The widest turn count in the catalog — the ceiling the range inputs advertise
 * so a reader knows what "max" is bounded by. `0` when nothing is counted.
 */
export function maxTurnCount(sessions: readonly SessionSummaryView[]): number {
  return sessions.reduce((widest, session) => Math.max(widest, turnCountOf(session) ?? 0), 0);
}

/**
 * A typed bound, normalized. An empty box means "unbounded", not zero — so
 * clearing the field must restore the full list rather than filter it to
 * nothing.
 *
 * `clamp` is `elements/range.ts`'s, which is where this app already decided
 * what a numeric prop out of range means: NaN resolves to the FLOOR, so a
 * half-typed `-` or `e` reads as 0 instead of poisoning every comparison with
 * NaN. There is deliberately no upper clamp — a bound above the widest count
 * is a legitimate thing to type on the way to a smaller one, and snapping it
 * down mid-keystroke fights the person typing it. Overshooting simply matches
 * nothing, which the count under the table already says.
 */
export function parseBound(raw: string): number | null {
  if (raw.trim() === "") return null;
  return Math.floor(clamp(Number(raw), 0, Number.MAX_SAFE_INTEGER));
}

/**
 * How the browser states its size — and, when it is not showing everything,
 * says so.
 *
 * Split out for the reason the fleet's is: "1 sessions" is invisible in a
 * template literal and obvious in a test.
 *
 * `capped` is the fix for a silent cap. `GET /sessions` slices to a `limit` and
 * returns no grand total, so a host with several hundred sessions answered
 * exactly the limit and this line read "50 sessions" — a full stop, which says
 * *this is everything*. The client cannot honestly print "50 of 312" because
 * nobody told it 312; what it can say is which end of the list it holds, and
 * the catalog is newest-first, so "the newest 200 on this host" is both true
 * and the thing a reader needs in order to know to search rather than scroll.
 */
export function sessionCountLabel(shown: number, total: number, capped = false): string {
  const noun = total === 1 ? "session" : "sessions";
  const base = shown === total ? `${total} ${noun}` : `${shown} of ${total} ${noun}`;
  return capped ? `${base} — the newest ${total} on this host` : base;
}
