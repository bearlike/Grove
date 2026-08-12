/**
 * Faceted filtering, as a MECHANISM. What to facet on is each surface's policy.
 *
 * The fleet narrows workspaces by agent state and project; the session browser
 * narrows sessions by location, agent and branch. Those are different entities
 * with no shared row type, so one `admits()` cannot serve both — but the SHAPE
 * is identical, and it is the shape that keeps getting re-derived: a hide set,
 * a count beside every option, and the invariant below.
 *
 * THE INVARIANT, which is the whole reason this is a hide set and not a show
 * set: an empty filter shows everything, AND a value that streams in later is
 * visible by default. A show set freezes the menu's contents at the moment it
 * was first opened, so the next session in a repo the user has never seen is
 * silently absent — filtered out by a choice nobody made.
 */

/** The values of one dimension that are currently switched OFF. */
export type HideSet = readonly string[];

/**
 * One option in a filter menu, with the count it would show.
 *
 * `Id` is generic so a dimension whose values are a literal union — an agent
 * state, say — keeps that union all the way through the count and back out,
 * instead of widening to `string` and needing a cast at the far end.
 */
export interface Facet<Id extends string = string> {
  readonly id: Id;
  readonly label: string;
  readonly count: number;
}

export function shows(hidden: HideSet, id: string): boolean {
  return !hidden.includes(id);
}

/** Toggle one value. Returns a new set; the caller owns the state. */
export function toggleHidden(hidden: HideSet, id: string): HideSet {
  return hidden.includes(id) ? hidden.filter((held) => held !== id) : [...hidden, id];
}

/**
 * Every distinct value of one dimension, with how many rows carry it.
 *
 * COUNTED OVER THE UNFILTERED ROWS, always. Counting the filtered set makes
 * every option read `0` the moment it is switched off, and then there is no way
 * to tell a category that is empty from one you have just hidden.
 *
 * `of` returns `null` for a row the dimension does not apply to — a session with
 * no branch is not a session on a branch called "unknown" — and those rows are
 * counted by nothing rather than by a fabricated bucket. Values collapse by
 * `id`, so a dimension keyed on a path can carry a friendlier `label` without
 * splitting into one option per spelling.
 */
export function facetCounts<T, Id extends string = string>(
  rows: readonly T[],
  of: (row: T) => { id: Id; label: string } | null,
): Facet<Id>[] {
  const counts = new Map<Id, Facet<Id>>();
  for (const row of rows) {
    const value = of(row);
    if (!value) continue;
    const seen = counts.get(value.id);
    counts.set(
      value.id,
      seen ? { ...seen, count: seen.count + 1 } : { id: value.id, label: value.label, count: 1 },
    );
  }
  return [...counts.values()];
}
