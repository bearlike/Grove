import { describe, expect, it } from "vitest";

import {
  activeFilterCount,
  admits,
  filterRows,
  fleetFacets,
  lastActivityAt,
  lastActivityIso,
  NO_FILTER,
  sortedFleetRows,
  workspaceCountLabel,
} from "@/components/grove/fleet/filter";
import { agentBrand } from "@/components/grove/fleet/tokens";
import { ACCOUNT_ITEMS, NAV_ITEMS, RAIL_ITEMS, sectionFor } from "@/components/grove/shell/nav";
import { project, snapshot, workspace } from "@/tests/fixtures/fleet";

const FLEET = snapshot([
  project("grove", "/repos/grove", [
    workspace({ id: "oldest", lastEventAt: "2026-08-01T09:00:00Z" }),
    workspace({ id: "newest", lastEventAt: "2026-08-11T09:00:00Z", state: "working" }),
  ]),
  project("docs", "/repos/docs", [
    workspace({
      id: "middle",
      lastEventAt: "2026-08-05T09:00:00Z",
      needsAttention: true,
      state: "waiting",
      agentName: "codex",
    }),
  ]),
]);

const ids = (rows: readonly { workspace: { state: { id: string } } }[]): string[] =>
  rows.map((row) => row.workspace.state.id);

describe("sortedFleetRows", () => {
  it("flattens every project into one list, newest activity first", () => {
    expect(ids(sortedFleetRows(FLEET))).toEqual(["newest", "middle", "oldest"]);
  });

  it("is empty, not thrown, for a snapshot that has not arrived", () => {
    expect(sortedFleetRows(undefined)).toEqual([]);
  });

  /**
   * The wall and the rail both render exactly these rows, so an empty repo
   * contributing nothing here is what stops it occupying a heading, a count and
   * a "No workspaces yet" line on screen — the regression this replaced.
   */
  it("gives an empty project no representation at all", () => {
    const mostlyEmpty = snapshot([
      project("agents", "/repos/agents", []),
      project("assistant", "/repos/assistant", []),
      project("grove", "/repos/grove", [workspace({ id: "only" })]),
      project("secrets", "/repos/secrets", []),
    ]);
    expect(ids(sortedFleetRows(mostlyEmpty))).toEqual(["only"]);
  });

  it("interleaves repos by recency rather than keeping them adjacent", () => {
    const mixed = snapshot([
      project("grove", "/repos/grove", [
        workspace({ id: "grove-old", lastEventAt: "2026-08-01T00:00:00Z" }),
        workspace({ id: "grove-new", lastEventAt: "2026-08-10T00:00:00Z" }),
      ]),
      project("docs", "/repos/docs", [
        workspace({ id: "docs-mid", lastEventAt: "2026-08-05T00:00:00Z" }),
      ]),
    ]);
    expect(ids(sortedFleetRows(mixed))).toEqual(["grove-new", "docs-mid", "grove-old"]);
  });

  it("breaks identical activity instants by workspace id, regardless of snapshot order", () => {
    const at = "2026-08-11T09:00:00Z";
    const forward = snapshot([
      project("grove", "/repos/grove", [
        workspace({ id: "bravo", lastEventAt: at }),
        workspace({ id: "alpha", lastEventAt: at }),
      ]),
    ]);
    const reverse = snapshot([
      project("grove", "/repos/grove", [
        workspace({ id: "alpha", lastEventAt: at }),
        workspace({ id: "bravo", lastEventAt: at }),
      ]),
    ]);

    expect(ids(sortedFleetRows(forward))).toEqual(["alpha", "bravo"]);
    expect(ids(sortedFleetRows(reverse))).toEqual(["alpha", "bravo"]);
  });

  it("does not re-rank two workspaces active within the same minute", () => {
    // The reported defect: with one or two live agents the rail reordered
    // itself several times a second, because `lastActivityAt` advances on every
    // event and the list is sorted by it. Ranking is bucketed to the minute, so
    // a seconds-apart advance does NOT move a row.
    //
    // `bravo` is genuinely NEWER here and still sorts second — that is the
    // trade being pinned, not an accident: inside one bucket the order is the
    // stable id, and a rail that holds still is worth more than a strict
    // ordering nobody can read at that timescale.
    const rows = (bravoAt: string) =>
      ids(
        sortedFleetRows(
          snapshot([
            project("grove", "/repos/grove", [
              workspace({ id: "alpha", lastEventAt: "2026-08-11T09:00:00Z" }),
              workspace({ id: "bravo", lastEventAt: bravoAt }),
            ]),
          ]),
        ),
      );

    expect(rows("2026-08-11T09:00:01Z")).toEqual(["alpha", "bravo"]);
    expect(rows("2026-08-11T09:00:30Z")).toEqual(["alpha", "bravo"]);
    expect(rows("2026-08-11T09:00:59Z")).toEqual(["alpha", "bravo"]);
  });

  it("still promotes a workspace once it crosses into a newer minute", () => {
    // The bucket must not become "recency no longer matters". A real gap still
    // reorders, which is what keeps the rail's whole premise true.
    const rows = ids(
      sortedFleetRows(
        snapshot([
          project("grove", "/repos/grove", [
            workspace({ id: "alpha", lastEventAt: "2026-08-11T09:00:00Z" }),
            workspace({ id: "bravo", lastEventAt: "2026-08-11T09:01:00Z" }),
          ]),
        ]),
      ),
    );
    expect(rows).toEqual(["bravo", "alpha"]);
  });
});

describe("workspaceCountLabel", () => {
  it("agrees with the count", () => {
    expect(workspaceCountLabel(1, 1)).toBe("1 workspace");
    expect(workspaceCountLabel(3, 3)).toBe("3 workspaces");
    expect(workspaceCountLabel(0, 0)).toBe("0 workspaces");
  });

  it("says how much of the fleet is showing while a filter is narrowing it", () => {
    expect(workspaceCountLabel(0, 1)).toBe("0 of 1 workspace");
    expect(workspaceCountLabel(2, 5)).toBe("2 of 5 workspaces");
  });
});

describe("lastActivityIso", () => {
  // The rail SORTS by this and now DISPLAYS it too. If the two ever read
  // different fields, the ages print in an order that contradicts the order
  // they are printed in, which reads as a broken clock rather than as the
  // deliberate fallback chain it is.
  it("returns the same instant the sort key uses", () => {
    const one = workspace({
      id: "w",
      lastEventAt: "2026-08-09T00:00:00Z",
      updatedAt: "2026-08-02T00:00:00Z",
    });
    expect(Date.parse(lastActivityIso(one)!)).toBe(lastActivityAt(one));
  });

  it("hands back the ORIGINAL string, so the display formats what the sort ranked", () => {
    const one = workspace({ id: "w", lastEventAt: "2026-08-09T00:00:00Z" });
    expect(lastActivityIso(one)).toBe("2026-08-09T00:00:00Z");
  });

  it("is null only when the workspace carries no usable timestamp at all", () => {
    const bare = workspace({ id: "w", hasSession: false, createdAt: "", updatedAt: "" });
    expect(lastActivityIso(bare)).toBeNull();
    expect(lastActivityAt(bare)).toBe(0);
  });
});

describe("lastActivityAt", () => {
  it("prefers the agent's own last event", () => {
    const at = lastActivityAt(
      workspace({ id: "w", lastEventAt: "2026-08-09T00:00:00Z", updatedAt: "2026-08-02T00:00:00Z" }),
    );
    expect(at).toBe(Date.parse("2026-08-09T00:00:00Z"));
  });

  it("falls back to the record's own timestamps when no agent has reported", () => {
    const at = lastActivityAt(
      workspace({
        id: "w",
        hasSession: false,
        createdAt: "2026-08-03T00:00:00Z",
        updatedAt: "2026-08-04T00:00:00Z",
      }),
    );
    expect(at).toBe(Date.parse("2026-08-04T00:00:00Z"));
  });

  it("sorts a brand-new session-less workspace to the top, where its creator is looking", () => {
    const fresh = snapshot([
      project("grove", "/repos/grove", [
        workspace({ id: "old", lastEventAt: "2026-08-01T00:00:00Z" }),
        workspace({ id: "just-made", hasSession: false, createdAt: "2026-08-11T12:00:00Z" }),
      ]),
    ]);
    expect(ids(sortedFleetRows(fresh))[0]).toBe("just-made");
  });
});

describe("the filter predicate", () => {
  const rows = sortedFleetRows(FLEET);

  it("admits everything when nothing is set", () => {
    expect(ids(filterRows(rows, NO_FILTER))).toEqual(["newest", "middle", "oldest"]);
  });

  it("matches the query against title, branch, repo and agent", () => {
    expect(ids(filterRows(rows, { ...NO_FILTER, query: "docs" }))).toEqual(["middle"]);
    expect(ids(filterRows(rows, { ...NO_FILTER, query: "codex" }))).toEqual(["middle"]);
    expect(ids(filterRows(rows, { ...NO_FILTER, query: "feat/newest" }))).toEqual(["newest"]);
  });

  it("keeps only the workspaces that want a human", () => {
    expect(ids(filterRows(rows, { ...NO_FILTER, attentionOnly: true }))).toEqual(["middle"]);
  });

  it("treats states and projects as HIDE sets, so a new one is visible by default", () => {
    expect(ids(filterRows(rows, { ...NO_FILTER, hiddenStates: ["working"] }))).toEqual([
      "middle",
      "oldest",
    ]);
    expect(ids(filterRows(rows, { ...NO_FILTER, hiddenProjects: ["/repos/docs"] }))).toEqual([
      "newest",
      "oldest",
    ]);
  });

  it("stacks criteria additively", () => {
    const filter = { ...NO_FILTER, attentionOnly: true, hiddenProjects: ["/repos/docs"] };
    expect(filterRows(rows, filter)).toEqual([]);
  });

  it("reads a session-less workspace as `unknown`, not as a missing row", () => {
    const sessionless = { workspace: workspace({ id: "w", hasSession: false }), repoName: "g", repoRoot: "/g" };
    expect(admits(sessionless, { ...NO_FILTER, hiddenStates: ["unknown"] })).toBe(false);
    expect(admits(sessionless, NO_FILTER)).toBe(true);
  });
});

describe("activeFilterCount", () => {
  it("ignores the query, which has its own visible input", () => {
    expect(activeFilterCount({ ...NO_FILTER, query: "anything" })).toBe(0);
  });

  it("counts every hidden state and project alongside the attention toggle", () => {
    expect(
      activeFilterCount({
        query: "",
        attentionOnly: true,
        hiddenStates: ["idle", "error"],
        hiddenProjects: ["/repos/docs"],
      }),
    ).toBe(4);
  });
});

describe("fleetFacets", () => {
  const facets = fleetFacets(sortedFleetRows(FLEET));

  it("counts the UNFILTERED fleet, so a hidden category still shows its size", () => {
    expect(facets.attention).toBe(1);
    expect(facets.states).toEqual([
      { state: "working", count: 1 },
      { state: "waiting", count: 1 },
      { state: "idle", count: 1 },
    ]);
  });

  it("lists each repo once, with its workspace count", () => {
    expect(facets.projects).toEqual([
      { repoRoot: "/repos/grove", repoName: "grove", count: 2 },
      { repoRoot: "/repos/docs", repoName: "docs", count: 1 },
    ]);
  });

  it("collapses nested project groups that share a repo root", () => {
    const nested = snapshot([
      project("grove", "/repos/grove", [workspace({ id: "a" })]),
      project("grove", "/repos/grove", [workspace({ id: "b" })]),
    ]);
    expect(fleetFacets(sortedFleetRows(nested)).projects).toEqual([
      { repoRoot: "/repos/grove", repoName: "grove", count: 2 },
    ]);
  });
});

describe("agentBrand", () => {
  it("resolves the vendors we hold a mark for", () => {
    expect(agentBrand("claude")).toBe("claude");
    expect(agentBrand("Claude Code")).toBe("claude");
    expect(agentBrand("codex")).toBe("codex");
    expect(agentBrand("gpt-5-codex")).toBe("codex");
    expect(agentBrand("gpt-4")).toBe("openai");
    expect(agentBrand("gemini-cli")).toBe("gemini");
  });

  it("falls back to a neutral mark rather than guessing a brand", () => {
    expect(agentBrand("bash")).toBe("generic");
    expect(agentBrand("")).toBe("generic");
  });
});

describe("sectionFor", () => {
  it("titles a listed route with its own label", () => {
    expect(sectionFor("/usage").label).toBe("Usage");
    expect(sectionFor("/sessions").label).toBe("All Sessions");
  });

  it("titles a nested route with its section", () => {
    // The invariant is that a nested route resolves to the SAME section as its
    // root, not what that section happens to be spelled — the name is a product
    // decision and moved once already.
    expect(sectionFor("/sessions/abc123")).toBe(sectionFor("/sessions"));
  });

  it("keeps titling a section the RAIL no longer offers", () => {
    // The regression this guards: Sessions moved into the account menu, and the
    // cheap way to do that is to delete it from NAV_ITEMS — which silently
    // drops every /sessions route through to Fleet, so the page titles itself
    // with a section it is not in. Placement is chrome; the section is the
    // route.
    expect(RAIL_ITEMS.some((item) => item.href === "/sessions")).toBe(false);
    expect(ACCOUNT_ITEMS.some((item) => item.href === "/sessions")).toBe(true);
    expect(sectionFor("/sessions/abc123").label).not.toBe("Fleet");
  });

  it("offers every destination exactly once, somewhere", () => {
    expect([...RAIL_ITEMS, ...ACCOUNT_ITEMS].map((item) => item.href).sort()).toEqual(
      NAV_ITEMS.filter((item) => item.href !== "/")
        .map((item) => item.href)
        .sort(),
    );
  });

  it("falls back to the first destination for the root and for anything unlisted", () => {
    // `/` is Launch now, and the fallback is deliberately positional — the head
    // of NAV_ITEMS — rather than a hard-coded section name. Pinning "Fleet"
    // here would fail every time the landing surface changes, which says
    // nothing about whether the fallback still works.
    expect(sectionFor("/").label).toBe(NAV_ITEMS[0]!.label);
    expect(sectionFor("/w/some-workspace").label).toBe(NAV_ITEMS[0]!.label);
  });
});
