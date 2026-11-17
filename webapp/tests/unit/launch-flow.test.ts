import { describe, expect, it } from "vitest";

import {
  LAUNCH_INITIAL_STATE,
  launchReducer,
  type LaunchValues,
} from "@/components/grove/launch/launch-state";
import { buildCreateRequest, openProblems } from "@/lib/grove/adapters/launch";
import {
  agentTools,
  draftSources,
  launchSummary,
  placementOf,
  problemFor,
  profileForTool,
} from "@/lib/grove/adapters/launch-flow";
import type { AgentSummaryView } from "@/lib/grove/api";
import { agentSummary } from "@/tests/fixtures/agents";

function agent(name: string, kind: AgentSummaryView["kind"]): AgentSummaryView {
  return agentSummary(name, kind);
}

const ROSTER = [
  agent("shell", "generic"),
  agent("claude", "claude_code"),
  agent("codex", "codex"),
  agent("claude-gateway", "claude_code"),
];

describe("tools are grouped from the roster, never declared", () => {
  it("groups every entry under its kind, in the order each kind first appears", () => {
    const tools = agentTools(ROSTER);
    expect(tools.map((tool) => tool.kind)).toEqual(["generic", "claude_code", "codex"]);
    expect(tools[1]!.profiles.map((profile) => profile.name)).toEqual(["claude", "claude-gateway"]);
  });

  it("names a kind with the shared word, and prints an unknown kind verbatim", () => {
    const tools = agentTools([agent("x", "claude_code"), agent("y", "generic")]);
    expect(tools[0]!.label).toBe("Claude");
    // `generic` has no entry in the shared table: the wire word, not a guess.
    expect(tools[1]!.label).toBe("generic");
  });

  it("has no profile question for a tool with one entry", () => {
    expect(agentTools(ROSTER).find((tool) => tool.kind === "codex")!.profiles).toHaveLength(1);
  });
});

describe("switching tool keeps the person's profile when it belongs to that tool", () => {
  const claude = agentTools(ROSTER).find((tool) => tool.kind === "claude_code")!;

  it("keeps the entry already chosen", () => {
    expect(profileForTool(claude, "claude-gateway")).toBe("claude-gateway");
  });

  it("lands on the tool's first entry when the current one is another tool's", () => {
    expect(profileForTool(claude, "codex")).toBe("claude");
  });

  it("sends the chosen profile as the agent identity, nothing else", () => {
    // Grouping is presentation: the request names the roster entry, so a
    // profile is never a second identity that can disagree with agent_name.
    const ready = launchReducer(
      launchReducer(LAUNCH_INITIAL_STATE, {
        type: "set",
        values: { repoRoot: "/r", agentName: profileForTool(claude, "codex") },
      }),
      { type: "seed", values: {}, repo: "/r" },
    );
    const request = buildCreateRequest(ready, "go");
    expect(request.agent_name).toBe("claude");
    expect(Object.keys(request)).not.toContain("profile");
  });
});

describe("placement is current checkout or an isolated worktree", () => {
  it("reads root as the current checkout and every other mode as isolated", () => {
    expect(placementOf("root")).toBe("current");
    for (const mode of ["auto", "new", "existing", "remote"] as const) {
      expect(placementOf(mode), mode).toBe("isolated");
    }
  });

  it("sends root for the current checkout and an auto branch when isolated", () => {
    const pick = (branchMode: LaunchValues["branchMode"]) =>
      buildCreateRequest(
        launchReducer(
          launchReducer(LAUNCH_INITIAL_STATE, {
            type: "set",
            values: { repoRoot: "/r", agentName: "claude", branchMode },
          }),
          { type: "seed", values: {}, repo: "/r" },
        ),
        "go",
      ).branch_plan;
    expect(pick("root")).toEqual({ kind: "root" });
    expect(pick("auto")).toMatchObject({ kind: "auto" });
  });
});

describe("the summary admonition", () => {
  const values = { runtime: "host", branchMode: "root", agentName: "claude", baseRef: null } as const;

  it("titles the runtime and says which checkout, with no source it was not given", () => {
    expect(launchSummary({ values, projectLabel: "widget", runtimeLabel: "this host" })).toEqual({
      title: "Runs on this host",
      where: "In widget's current checkout, with no new branch or worktree.",
      provenance: null,
    });
  });

  it("says isolated, and from which base, when it is", () => {
    expect(
      launchSummary({
        values: { ...values, branchMode: "auto", baseRef: "main" },
        projectLabel: "widget",
        runtimeLabel: "a Dev Container",
      }),
    ).toMatchObject({ title: "Runs on a Dev Container", where: "In an isolated worktree of widget from main." });
  });

  it.each(["new", "existing", "remote"] as const)(
    "reads %s as isolated too — every non-root mode is a worktree",
    (branchMode) => {
      // `auto` alone cannot tell "isolated" from "auto": these are the modes
      // a rule keyed on the wrong value would misreport as the checkout.
      expect(
        launchSummary({ values: { ...values, branchMode }, projectLabel: "widget", runtimeLabel: "this host" })?.where,
      ).toContain("isolated worktree");
    },
  );

  it("names each source once, with the fields it answered", () => {
    expect(
      launchSummary({
        values,
        projectLabel: "widget",
        runtimeLabel: "this host",
        sources: { runtime: "user", branch_plan: "user", agent_name: "project" },
      })?.provenance,
    ).toBe("Your default: runtime, placement · Project default: agent");
  });

  it("says nothing while the project or runtime is unresolved", () => {
    expect(launchSummary({ values, projectLabel: null, runtimeLabel: "this host" })).toBeNull();
    expect(launchSummary({ values, projectLabel: "widget", runtimeLabel: null })).toBeNull();
  });
});

describe("the engine's problems refuse Send until the owning field is answered", () => {
  const INCOMPLETE = {
    field: "branch_plan",
    error: "incomplete_default",
    message: "The saved branch mode `new` names no branch; choose one for this workspace.",
  };
  const seeded = (problems: typeof INCOMPLETE[]) =>
    launchReducer(
      launchReducer(LAUNCH_INITIAL_STATE, { type: "set", values: { repoRoot: "/r" } }),
      { type: "seed", values: { branchMode: "new" }, repo: "/r", problems },
    );

  it("refuses with the engine's own sentence, before any request exists", () => {
    expect(() => buildCreateRequest(seeded([INCOMPLETE]), "go")).toThrow(INCOMPLETE.message);
  });

  it("lands on the control that owns the field", () => {
    const state = seeded([INCOMPLETE]);
    expect(problemFor(openProblems(state), "branchMode")).toBe(INCOMPLETE.message);
    expect(problemFor(openProblems(state), "runtime")).toBeNull();
  });

  it("clears once the person answers that field, and the answer is sent", () => {
    // Touching it means the request carries an explicit choice, so the
    // problem — which was about the SAVED answer — no longer describes it.
    const answered = launchReducer(seeded([INCOMPLETE]), {
      type: "set",
      values: { branchMode: "new", branchName: "feat/x" },
    });
    expect(openProblems(answered)).toEqual([]);
    expect(buildCreateRequest(answered, "go").branch_plan).toMatchObject({
      kind: "new_named",
      name: "feat/x",
    });
  });

  it("keeps a problem about a field no control edits, so it still refuses", () => {
    const unmapped = { field: "selector", error: "x", message: "Selector is wrong" };
    const state = seeded([unmapped]);
    expect(openProblems(state)).toEqual([unmapped]);
  });

  it("does not loop the seed on an equal but re-parsed problem list", () => {
    const state = seeded([INCOMPLETE]);
    const again = launchReducer(state, {
      type: "seed",
      values: { branchMode: "new" },
      repo: "/r",
      problems: [{ ...INCOMPLETE }],
    });
    expect(again).toBe(state);
  });

  it("replaces the problems when the project's answer changes", () => {
    const state = seeded([INCOMPLETE]);
    const fixed = launchReducer(state, { type: "seed", values: {}, repo: "/r", problems: [] });
    expect(fixed.problems).toEqual([]);
    expect(() => buildCreateRequest(fixed, "go")).not.toThrow();
  });
});

describe("provenance follows the draft, not just the defaults", () => {
  const SOURCES = { runtime: "project", branch_plan: "builtin", agent_name: "config" } as const;

  it("passes the engine's source for an untouched field", () => {
    expect(draftSources(SOURCES, new Set())).toEqual({
      runtime: "project",
      branch_plan: "builtin",
      agent_name: "config",
    });
  });

  it("attributes a changed field to the request, whatever the default said", () => {
    expect(draftSources(SOURCES, new Set(["runtime", "branchMode"]))).toEqual({
      runtime: "request",
      branch_plan: "request",
      agent_name: "config",
    });
  });

  it("states no source the engine did not give", () => {
    expect(draftSources(undefined, new Set())).toEqual({});
  });
});
