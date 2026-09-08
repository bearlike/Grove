import { describe, expect, it } from "vitest";

import type { AgentActivityView, TokenClassesView, WorkspaceActivityView } from "@/lib/grove/api";
import { activityFacts } from "@/components/grove/workspace/selectors";

/**
 * The Info tab's "tokens in" figure, and why it stopped being one alarming
 * number: `AgentActivityView.tokens_in` folds fresh input, cache read and
 * cache creation together BY DESIGN (see its engine docstring), so a figure
 * in the hundreds of millions is correct and unexplained at the same time.
 * `activityFacts` unfolds it only when the wire carries the breakdown. The
 * failure this guards against is the two rendering TOGETHER (restating one
 * magnitude twice) or a null class rendering as a fabricated `0`.
 */

function liveActivity(partial: Partial<AgentActivityView>): AgentActivityView {
  return {
    human_turns: 3,
    tool_calls: 5,
    tokens_in: 262_000_000,
    tokens_out: 4_000,
    ...partial,
  } as AgentActivityView;
}

function workspaceActivity(
  activity: AgentActivityView | undefined,
  tokens?: TokenClassesView | null,
): WorkspaceActivityView {
  // Only the fields under test are real; the rest of the row never reaches
  // these selectors.
  return { sessions: activity ? [{ activity, tokens }] : [] } as unknown as WorkspaceActivityView;
}

describe("activityFacts", () => {
  it("is null with no live session", () => {
    expect(activityFacts(null)).toBeNull();
    expect(activityFacts(workspaceActivity(undefined))).toBeNull();
  });

  it("uses the folded total, and only the folded total, without a class breakdown", () => {
    const facts = activityFacts(workspaceActivity(liveActivity({}), null));
    const keys = facts?.map((fact) => fact.key);

    expect(keys).toEqual(["turns", "tool_calls", "output", "input_total"]);
    expect(keys).toContain("input_total");
    expect(keys).not.toEqual(expect.arrayContaining(["fresh_input", "cache_read", "cache_write"]));
  });

  it("replaces the folded total with all three classes when the wire reports them", () => {
    const tokens: TokenClassesView = {
      fresh_input: 1_200,
      cache_read: 261_000_000,
      cache_creation: 900_000,
    };
    const facts = activityFacts(workspaceActivity(liveActivity({}), tokens));
    const keys = facts?.map((fact) => fact.key);

    expect(keys).toEqual(["turns", "tool_calls", "output", "fresh_input", "cache_read", "cache_write"]);
    expect(keys).not.toContain("input_total");
    expect(keys).toEqual(expect.arrayContaining(["fresh_input", "cache_read", "cache_write"]));
  });

  it("keeps an unmeasured class distinct from a class whose reported value is zero", () => {
    const tokens: TokenClassesView = {
      fresh_input: 0,
      cache_read: null,
      cache_creation: undefined,
    };
    const facts = activityFacts(workspaceActivity(liveActivity({}), tokens));

    expect(facts?.find((fact) => fact.key === "fresh_input")).toMatchObject({
      value: "0",
      derived: true,
    });
    expect(facts?.find((fact) => fact.key === "cache_read")).toMatchObject({
      value: null,
      derived: false,
    });
    expect(facts?.find((fact) => fact.key === "cache_write")).toMatchObject({
      value: null,
      derived: false,
    });
  });

  it("puts glossary definitions on exactly the two cache classes", () => {
    const tokens: TokenClassesView = {
      fresh_input: 1_200,
      cache_read: 261_000_000,
      cache_creation: 900_000,
    };
    const facts = activityFacts(workspaceActivity(liveActivity({}), tokens));

    expect(facts?.filter((fact) => fact.term).map((fact) => [fact.key, fact.term])).toEqual([
      ["cache_read", "cache_read_tokens"],
      ["cache_write", "cache_creation_tokens"],
    ]);
  });

  it("keeps every abbreviated figure's exact grouped value one hover away", () => {
    const tokens: TokenClassesView = {
      fresh_input: 1_200,
      cache_read: 261_000_000,
      cache_creation: 900_000,
    };
    const facts = activityFacts(workspaceActivity(liveActivity({}), tokens));
    const exactValues = new Map([
      ["output", "4,000"],
      ["fresh_input", "1,200"],
      ["cache_read", "261,000,000"],
      ["cache_write", "900,000"],
    ]);

    for (const fact of facts ?? []) {
      const exact = exactValues.get(fact.key);
      if (exact) expect(fact.title, fact.key).toContain(exact);
    }
  });

  it("marks Grove's reported class sums as derived, but not the session's own counters", () => {
    const tokens: TokenClassesView = {
      fresh_input: 1_200,
      cache_read: 261_000_000,
      cache_creation: 900_000,
    };
    const facts = activityFacts(workspaceActivity(liveActivity({}), tokens));

    expect(facts?.filter((fact) => fact.derived).map((fact) => fact.key)).toEqual([
      "fresh_input",
      "cache_read",
      "cache_write",
    ]);
    expect(facts?.filter((fact) => !fact.derived).map((fact) => fact.key)).toEqual([
      "turns",
      "tool_calls",
      "output",
    ]);
  });
});
