import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { AgentActivityView, TokenClassesView, WorkspaceActivityView } from "@/lib/grove/api";
import { CardField, CardFields } from "@/components/grove/card";
import { Explain } from "@/components/grove/glossary";
import { activityStats, tokenClassStats } from "@/components/grove/workspace/selectors";

/**
 * The Info tab's "tokens in" figure, and why it stopped being one alarming
 * number: `AgentActivityView.tokens_in` folds fresh input, cache read and
 * cache creation together BY DESIGN (see its engine docstring), so a figure
 * in the hundreds of millions is correct and unexplained at the same time.
 * `tokenClassStats` unfolds it; `activityStats` falls back to the folded
 * total only when there is nothing to unfold. The failure this guards
 * against is the two rendering TOGETHER (restating one magnitude twice) or a
 * null class rendering as a fabricated `0`.
 */

function liveActivity(partial: Partial<AgentActivityView>): AgentActivityView {
  return { human_turns: 3, tool_calls: 5, tokens_in: 262_000_000, tokens_out: 4_000, ...partial } as AgentActivityView;
}

function workspaceActivity(
  activity: AgentActivityView | undefined,
  tokens?: TokenClassesView | null,
): WorkspaceActivityView {
  // Only the fields under test are real; the rest of the row never reaches
  // these selectors.
  return { sessions: activity ? [{ activity, tokens }] : [] } as unknown as WorkspaceActivityView;
}

describe("activityStats", () => {
  it("is null with no live session", () => {
    expect(activityStats(null)).toBeNull();
    expect(activityStats(workspaceActivity(undefined))).toBeNull();
  });

  it("includes the folded 'tokens in' when the wire carries no class breakdown", () => {
    const stats = activityStats(workspaceActivity(liveActivity({}), null));

    expect(stats?.map((s) => s.label)).toEqual(["turns", "tool calls", "tokens in", "tokens out"]);
    expect(stats?.find((s) => s.label === "tokens in")?.value).toBe("262M");
  });

  it("drops the folded 'tokens in' the moment a class breakdown is present — never both", () => {
    const tokens: TokenClassesView = { fresh_input: 100, cache_read: 261_000_000, cache_creation: 900_000 };

    const stats = activityStats(workspaceActivity(liveActivity({}), tokens));

    expect(stats?.map((s) => s.label)).toEqual(["turns", "tool calls", "tokens out"]);
  });
});

describe("tokenClassStats", () => {
  it("is null when the wire carries no breakdown (older daemon, or no message spine to reduce)", () => {
    expect(tokenClassStats(null)).toBeNull();
    expect(tokenClassStats(workspaceActivity(liveActivity({}), null))).toBeNull();
    expect(tokenClassStats(workspaceActivity(liveActivity({}), undefined))).toBeNull();
  });

  it("reports the three classes that sum to `tokens_in`, abbreviated", () => {
    const tokens: TokenClassesView = { fresh_input: 1_200, cache_read: 261_000_000, cache_creation: 900_000 };

    const rows = tokenClassStats(workspaceActivity(liveActivity({}), tokens));

    expect(rows).toEqual([
      { label: "fresh input", value: "1.2K" },
      { label: "cache read", value: "261M", term: "cache_read_tokens" },
      { label: "cache write", value: "900K", term: "cache_creation_tokens" },
    ]);
  });

  it("never fabricates a zero — a class nothing reported renders 'not measured'", () => {
    const tokens: TokenClassesView = { fresh_input: 500, cache_read: null, cache_creation: null };

    const rows = tokenClassStats(workspaceActivity(liveActivity({}), tokens));

    expect(rows?.find((r) => r.label === "fresh input")?.value).toBe("500");
    expect(rows?.find((r) => r.label === "cache read")?.value).toBe("not measured");
    expect(rows?.find((r) => r.label === "cache write")?.value).toBe("not measured");
  });
});

describe("a token-class row's label", () => {
  it("carries its own definition for cache read and cache write, the two classes that dwarf fresh input", () => {
    const html = renderToStaticMarkup(
      <CardFields>
        <CardField label={<Explain term="cache_read_tokens" />}>261M</CardField>
        <CardField label={<Explain term="cache_creation_tokens" />}>900K</CardField>
      </CardFields>,
    );

    expect(html).toContain('data-testid="explain-cache_read_tokens"');
    expect(html).toContain('data-testid="explain-cache_creation_tokens"');
    expect(html).toContain("Cache write");
  });
});
