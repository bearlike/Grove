import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { facetCounts, shows, toggleHidden } from "@/components/grove/facets";
import {
  activeSessionFilterCount,
  admits,
  filterSessions,
  isBareLocation,
  locationOf,
  matchesQuery,
  maxTurnCount,
  NO_SESSION_FILTER,
  parseBound,
  relativeCwd,
  sessionCountLabel,
  sessionFacets,
  turnCountOf,
  withinTurnBounds,
} from "@/components/grove/sessions/filter";
import {
  CAPPED_CELL,
  CAPPED_COL,
  FROZEN_TABLE_HEAD,
  LABEL_COL,
} from "@/components/grove/table-columns";
import type { SessionSummaryView } from "@/lib/grove/api";

/**
 * A catalog row as the live daemon actually returns one.
 *
 * The null-heavy defaults are not laziness: a host-scoped scan is one bounded
 * head read per session, so `title`, `first_prompt`, `activity` and
 * `size_bytes` come back null on EVERY row, while `adapter_kind`, `git_branch`
 * and `cwd` come back populated. Filtering rules written against a fixture with
 * friendly titles would be rules for data that does not exist.
 *
 * `turn_count` is typed loosely here because it is landing on
 * `SessionSummaryView` while this is being written — same reason `turnCountOf`
 * reads it structurally.
 */
type Row = SessionSummaryView & { turn_count?: number | null };

function session(over: Partial<Row> = {}): Row {
  return {
    session_id: "b3b5108d-1cbf-4ccf-91f9-e1e7d515542c",
    adapter_kind: "claude_code",
    provenance: "fs_discovered",
    primary: false,
    workspace_id: null,
    workspace_title: null,
    workspace_branch: null,
    git_branch: "main",
    created_at: "2026-08-10T06:30:39.680000Z",
    modified_at: "2026-08-10T22:11:29.394068Z",
    size_bytes: null,
    title: null,
    first_prompt: null,
    last_prompt: null,
    activity: null,
    cwd: "/repos/grove",
    project: {
      repo_root: "/repos/grove",
      repo_name: "Grove",
      is_worktree: false,
      is_grove_managed: true,
    },
    live: false,
    ...over,
  };
}

describe("turn counts are nullable, and null is never zero", () => {
  it("reads a count the daemon reported", () => {
    expect(turnCountOf(session({ turn_count: 42 }))).toBe(42);
    expect(turnCountOf(session({ turn_count: 0 }))).toBe(0);
  });

  it("reads an absent or null count as NOT COUNTED, not as zero", () => {
    // The whole point: the host-wide scan does not open a transcript, so a
    // missing count means "not counted at this scope". A fabricated 0 would
    // claim the session did nothing.
    expect(turnCountOf(session())).toBeNull();
    expect(turnCountOf(session({ turn_count: null }))).toBeNull();
  });

  it("refuses a non-finite count rather than propagating NaN through comparisons", () => {
    expect(turnCountOf(session({ turn_count: Number.NaN }))).toBeNull();
  });
});

describe("the turn range is bidirectional and excludes the uncounted", () => {
  it("admits everything while both ends are unset", () => {
    expect(withinTurnBounds(null, null, null)).toBe(true);
    expect(withinTurnBounds(7, null, null)).toBe(true);
  });

  it("bounds from below, from above, and from both at once", () => {
    expect(withinTurnBounds(7, 10, null)).toBe(false);
    expect(withinTurnBounds(70, 10, null)).toBe(true);
    expect(withinTurnBounds(70, null, 10)).toBe(false);
    expect(withinTurnBounds(7, null, 10)).toBe(true);
    expect(withinTurnBounds(7, 5, 10)).toBe(true);
    expect(withinTurnBounds(11, 5, 10)).toBe(false);
  });

  it("is inclusive at both ends", () => {
    expect(withinTurnBounds(5, 5, 10)).toBe(true);
    expect(withinTurnBounds(10, 5, 10)).toBe(true);
  });

  it("HIDES an uncounted session while a bound is set, and never treats it as 0", () => {
    // A bound is a claim about a number; a row whose number is unknown cannot
    // satisfy it. Reading null as 0 would let "max 5 turns" match a session
    // that ran for hours.
    expect(withinTurnBounds(null, 1, null)).toBe(false);
    expect(withinTurnBounds(null, null, 5)).toBe(false);
    expect(withinTurnBounds(null, 0, null)).toBe(false);
  });
});

describe("a bound typed into the box", () => {
  it("reads an empty box as UNBOUNDED, so clearing it restores the list", () => {
    expect(parseBound("")).toBeNull();
    expect(parseBound("   ")).toBeNull();
  });

  it("keeps zero, which is a legitimate bound and not an empty box", () => {
    expect(parseBound("0")).toBe(0);
  });

  it("floors at zero but never clamps from above", () => {
    // Snapping a bound down mid-keystroke fights the person typing it, and
    // overshooting is self-correcting: the count under the table says nothing
    // matched.
    expect(parseBound("-4")).toBe(0);
    expect(parseBound("40")).toBe(40);
    expect(parseBound("999999")).toBe(999999);
  });

  it("never yields NaN from a half-typed value", () => {
    expect(parseBound("e")).toBe(0);
    expect(parseBound("-")).toBe(0);
  });
});

describe("narrowing the catalog", () => {
  const rows = [
    session({ session_id: "a", adapter_kind: "claude_code", git_branch: "main" }),
    session({
      session_id: "b",
      adapter_kind: "codex",
      git_branch: "feat/usage-audit",
      turn_count: 30,
    }),
    session({
      session_id: "c",
      adapter_kind: "claude_code",
      git_branch: "main",
      cwd: "/repos/agents/pa",
      project: {
        repo_root: "/repos/agents",
        repo_name: "Agents",
        is_worktree: false,
        is_grove_managed: true,
      },
      turn_count: 4,
    }),
  ];

  it("shows everything under the empty filter", () => {
    expect(filterSessions(rows, NO_SESSION_FILTER)).toHaveLength(3);
  });

  it("hides by location, agent and branch independently", () => {
    expect(
      filterSessions(rows, { ...NO_SESSION_FILTER, hiddenLocations: ["/repos/agents"] }),
    ).toHaveLength(2);
    expect(filterSessions(rows, { ...NO_SESSION_FILTER, hiddenAgents: ["codex"] })).toHaveLength(2);
    expect(filterSessions(rows, { ...NO_SESSION_FILTER, hiddenBranches: ["main"] })).toHaveLength(1);
  });

  it("combines criteria additively", () => {
    expect(
      filterSessions(rows, {
        ...NO_SESSION_FILTER,
        hiddenAgents: ["codex"],
        hiddenBranches: ["main"],
      }),
    ).toHaveLength(0);
  });

  it("applies the turn range, dropping the row that was never counted", () => {
    expect(filterSessions(rows, { ...NO_SESSION_FILTER, minTurns: 10 })).toHaveLength(1);
    expect(filterSessions(rows, { ...NO_SESSION_FILTER, maxTurns: 10 })).toHaveLength(1);
  });

  it("searches the fields a catalog row actually carries, id included", () => {
    // The id is the one field always present, and the one a bug report quotes.
    expect(matchesQuery(rows[0]!, "a")).toBe(true);
    expect(matchesQuery(rows[1]!, "codex")).toBe(true);
    expect(matchesQuery(rows[1]!, "usage-audit")).toBe(true);
    expect(matchesQuery(rows[2]!, "agents")).toBe(true);
    expect(matchesQuery(rows[0]!, "nothing-here")).toBe(false);
  });

  it("matches case-insensitively and treats a blank query as no query", () => {
    expect(matchesQuery(rows[1]!, "CODEX")).toBe(true);
    expect(matchesQuery(rows[0]!, "   ")).toBe(true);
  });

  it("counts a bounded range as ONE active criterion however many ends are set", () => {
    expect(activeSessionFilterCount(NO_SESSION_FILTER)).toBe(0);
    expect(activeSessionFilterCount({ ...NO_SESSION_FILTER, minTurns: 1 })).toBe(1);
    expect(activeSessionFilterCount({ ...NO_SESSION_FILTER, minTurns: 1, maxTurns: 9 })).toBe(1);
    expect(
      activeSessionFilterCount({
        ...NO_SESSION_FILTER,
        minTurns: 1,
        hiddenAgents: ["codex"],
        hiddenBranches: ["main"],
      }),
    ).toBe(3);
  });

  it("does not count the search box: it is beside the menu, not inside it", () => {
    expect(activeSessionFilterCount({ ...NO_SESSION_FILTER, query: "grove" })).toBe(0);
  });

  it("tells a bare directory from a repo, so the menu can mark them differently", () => {
    // Drawing a repo glyph over `/tmp/claude-1000/otelproof3` asserts something
    // the scan never established — seen on the real host before this existed.
    expect(isBareLocation({ id: "/repos/grove", label: "Grove" })).toBe(false);
    expect(isBareLocation({ id: "/tmp/scratch", label: "/tmp/scratch" })).toBe(true);
    expect(isBareLocation(locationOf(session({ project: null, cwd: "/tmp/scratch" }))!)).toBe(true);
    expect(isBareLocation(locationOf(session())!)).toBe(false);
  });

  it("names the worktree a session ran in, relative to its repo", () => {
    // Without it every Grove session in one repo reads identically, and a
    // search that matched the path looks like it matched nothing.
    expect(relativeCwd(session({ cwd: "/repos/grove/.worktrees/otel-x" }))).toBe(
      ".worktrees/otel-x",
    );
    expect(relativeCwd(session())).toBeNull();
    expect(relativeCwd(session({ project: null }))).toBeNull();
  });

  it("does not relativize a sibling path that merely shares a prefix", () => {
    // `/repos/groveyard` is not inside `/repos/grove`.
    expect(relativeCwd(session({ cwd: "/repos/groveyard/x" }))).toBeNull();
  });

  it("files a session with no repo under its own directory, not under a fake project", () => {
    expect(locationOf(session({ project: null, cwd: "/tmp/scratch" }))).toEqual({
      id: "/tmp/scratch",
      label: "/tmp/scratch",
    });
    expect(locationOf(session({ project: null, cwd: null }))).toBeNull();
    // A row with no location at all belongs to no bucket, so no bucket can
    // switch it off.
    expect(
      admits(session({ project: null, cwd: null }), {
        ...NO_SESSION_FILTER,
        hiddenLocations: ["/repos/grove"],
      }),
    ).toBe(true);
  });
});

describe("what the filter menu offers", () => {
  const rows = [
    session({ session_id: "a", adapter_kind: "claude_code", git_branch: "main" }),
    session({ session_id: "b", adapter_kind: "codex", git_branch: "main", turn_count: 30 }),
    session({ session_id: "c", adapter_kind: "codex", git_branch: null }),
  ];

  it("counts over the UNFILTERED rows, so a switched-off option never reads 0", () => {
    expect(sessionFacets(rows).agents).toEqual([
      { id: "codex", label: "codex", count: 2 },
      { id: "claude_code", label: "claude_code", count: 1 },
    ]);
  });

  it("orders options by count, because the useful one covers most of the list", () => {
    const counts = sessionFacets(rows).agents.map((facet) => facet.count);
    expect(counts).toEqual([...counts].sort((a, b) => b - a));
  });

  it("invents no bucket for a row the dimension does not apply to", () => {
    // A session with no branch is not a session on a branch called "unknown".
    expect(sessionFacets(rows).branches).toEqual([{ id: "main", label: "main", count: 2 }]);
  });

  it("reports how many rows an active range would hide, so the trade is visible", () => {
    expect(sessionFacets(rows).uncounted).toBe(2);
    expect(sessionFacets(rows).counted).toBe(1);
  });

  it("reports NOTHING counted when nothing is — the live host-scope case", () => {
    // The catalog scan leaves every `turn_count` null by design, so the menu
    // has to be able to tell "no counts exist" from "no counts survive the
    // filter" and decline to offer a range that would empty the table.
    const facets = sessionFacets([session(), session({ session_id: "z" })]);
    expect(facets.counted).toBe(0);
    expect(facets.uncounted).toBe(2);
  });

  it("advertises the widest count as the range ceiling", () => {
    expect(maxTurnCount(rows)).toBe(30);
    expect(maxTurnCount([session()])).toBe(0);
  });
});

describe("the shared facet kernel", () => {
  it("keeps an empty hide set meaning SHOW EVERYTHING", () => {
    expect(shows([], "anything")).toBe(true);
    expect(shows(["a"], "b")).toBe(true);
    expect(shows(["a"], "a")).toBe(false);
  });

  it("toggles without mutating, so state updates stay referentially honest", () => {
    const hidden = ["a"];
    expect(toggleHidden(hidden, "b")).toEqual(["a", "b"]);
    expect(toggleHidden(hidden, "a")).toEqual([]);
    expect(hidden).toEqual(["a"]);
  });

  it("collapses by id while keeping the friendlier label", () => {
    const rows = [
      { root: "/repos/grove", name: "Grove" },
      { root: "/repos/grove", name: "Grove" },
      { root: "/repos/agents", name: "Agents" },
    ];
    expect(facetCounts(rows, (row) => ({ id: row.root, label: row.name }))).toEqual([
      { id: "/repos/grove", label: "Grove", count: 2 },
      { id: "/repos/agents", label: "Agents", count: 1 },
    ]);
  });

  it("skips a row the dimension does not apply to", () => {
    expect(facetCounts([1, 2, 3], (n) => (n === 2 ? { id: "two", label: "two" } : null))).toEqual([
      { id: "two", label: "two", count: 1 },
    ]);
  });
});

describe("the list states its own size", () => {
  it("never prints '1 sessions'", () => {
    expect(sessionCountLabel(1, 1)).toBe("1 session");
    expect(sessionCountLabel(2, 2)).toBe("2 sessions");
  });

  it("says so when it is showing less than everything", () => {
    expect(sessionCountLabel(3, 40)).toBe("3 of 40 sessions");
  });
});

describe("FROZEN_TABLE_HEAD", () => {
  it("neutralises the vendored table's own scroll box", () => {
    // Without this the header's nearest scroll container is `Table`'s inner
    // `overflow-x-auto` div — which has no height bound, so it never scrolls
    // and a sticky header inside it never moves. Invisible in review, total in
    // effect.
    expect(FROZEN_TABLE_HEAD).toContain("[&_[data-slot=table-container]]:overflow-visible");
  });

  it("pins the header opaquely, on a theme token rather than a literal", () => {
    expect(FROZEN_TABLE_HEAD).toContain("[&_thead]:sticky");
    expect(FROZEN_TABLE_HEAD).toContain("[&_thead]:top-0");
    // Transparent by default means rows scroll THROUGH the header.
    expect(FROZEN_TABLE_HEAD).toContain("[&_thead]:bg-background");
    expect(FROZEN_TABLE_HEAD).not.toMatch(/#[0-9a-fA-F]{3,8}/);
  });

  it("owns the scroll itself", () => {
    expect(FROZEN_TABLE_HEAD).toContain("overflow-auto");
  });
});

describe("exactly one column may claim the remainder", () => {
  it("keeps the remainder and the capped column distinct", () => {
    // Two `LABEL_COL`s do not split the remainder — the auto layout gives it to
    // whichever has the wider content. Measured at 1600px before this split:
    // Location 845px, Branch 62px, which clipped `feat/usage-audit` to about
    // four characters. After: Location 576px, Branch 224px.
    expect(LABEL_COL).toBe("w-full");
    expect(CAPPED_COL).not.toContain("w-full");
  });

  it("caps the second long column so truncation can fire", () => {
    // Without a max-width one long branch name widens the column and takes the
    // space straight back off the remainder.
    expect(CAPPED_CELL).toMatch(/\bmax-w-/);
    expect(CAPPED_CELL).toContain("truncate");
  });
});

/**
 * SOURCE assertions for the archived-session route, for the reason the port
 * scan in `transcript-parts.test.tsx` gives: the correct render is the ABSENCE
 * of a local renderer, and this runner cannot import `Thread` at all (it pulls
 * a vendored CSS import). Comments are blanked first, because the page EXPLAINS
 * in prose what it no longer does.
 */
const SESSION = readFileSync("app/(shell)/sessions/[id]/page.tsx", "utf8")
  .replace(/\/\*[\s\S]*?\*\//g, "")
  .replace(/(^|[^:])\/\/[^\n]*/g, "$1");

describe("the archived session renders the workspace's transcript, not a copy", () => {
  it("mounts the ported Thread and the shared data-part registry", () => {
    expect(SESSION).toContain('from "@/components/grove/workspace/thread"');
    expect(SESSION).toContain('from "@/components/grove/workspace/data-parts"');
  });

  it("declares no message renderer of its own", () => {
    for (const primitive of ["MessagePrimitive", "ThreadPrimitive", "useAuiState"]) {
      expect(SESSION).not.toContain(primitive);
    }
  });

  it("never fabricates a workspace id to reach that machinery", () => {
    expect(SESSION).not.toContain("workspaceId");
    expect(SESSION).not.toContain("PendingQuestion");
  });

  it("stays read-only through the runtime capability, not a hidden control", () => {
    expect(SESSION).toContain("useReadOnlyTranscript");
  });

  it("keeps the missing-coordinate state, which a catalog row really can hit", () => {
    expect(SESSION).toContain("MissingCoordinate");
    expect(SESSION).toContain('search.get("kind")');
    expect(SESSION).toContain('search.get("cwd")');
  });

  it("sets no width or inset of its own, so it inherits the thread's column", () => {
    expect(SESSION).not.toContain("THREAD_WIDTH");
    expect(SESSION).not.toContain("THREAD_INSET");
    expect(SESSION).not.toContain("maxWidth");
  });
});
