import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { UsageToolSplit, toolSplit } from "@/components/grove/usage/tool-split";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { UsageBreakdownRowView, UsageBreakdownView } from "@/lib/grove/api";

/**
 * The card makes one claim — "MCP is N% of tool time" — and a percentage hides
 * its own denominator, so every test here is about what the denominator is
 * allowed to contain: not an untimed tool's fabricated zero, not a silent cap,
 * and not an outlier the reader would assume was work.
 *
 * The figures are this host's, measured 2026-08-11 against the real store, so a
 * regression in the fold shows up as a number a human recognises.
 */

const NO_TOKENS = {
  fresh_input: null,
  cache_read: null,
  cache_creation: null,
  reasoning: null,
  output: null,
  provider_total: null,
};

function row(
  key: string,
  tool_calls: number,
  active_ms: number | null,
): UsageBreakdownRowView {
  return {
    key,
    label: key,
    sessions: 1,
    tokens: NO_TOKENS,
    active_ms,
    tool_calls,
    tool_failures: 0,
    latency: { calls: 0 },
  };
}

const COVERAGE = {
  sources: [],
  degraded_source_count: 0,
  cost_available: false,
  quota_available: false,
};

function view(rows: UsageBreakdownRowView[], truncated = false): UsageBreakdownView {
  return { dimension: "tool", rows, truncated, coverage: COVERAGE };
}

/** The real shape of this host's top rows: a dominant Bash, the human-wait
 * outlier, and the two most expensive MCP servers. */
const REAL = view(
  [
    row("Bash", 92_789, 951_159_662),
    row("Agent", 2506, 189_454_462),
    row("AskUserQuestion", 168, 165_337_328),
    row("mcp__deepwiki__ask_question", 956, 20_139_282),
    row("mcp__gitea__Gitea-Hurricane-issue_write", 1462, 19_990_591),
  ],
  true,
);

function render(node: React.ReactNode): string {
  return renderToStaticMarkup(<TooltipProvider>{node}</TooltipProvider>);
}

describe("toolSplit", () => {
  it("classifies on the mcp__ name prefix and nothing else", () => {
    const split = toolSplit(REAL.rows);
    expect(split.builtin.tools).toBe(3);
    expect(split.mcp.tools).toBe(2);
    expect(split.mcp.calls).toBe(2418);
    expect(split.builtin.calls).toBe(95_463);
  });

  it("EXCLUDES an untimed tool from the time sum instead of adding it as zero", () => {
    const split = toolSplit([row("Bash", 100, 5000), row("Glob", 40, null)]);
    expect(split.builtin.ms).toBe(5000);
    expect(split.builtin.untimedTools).toBe(1);
    expect(split.builtin.untimedCalls).toBe(40);
    // The call split stays complete over the rows we were given; only the time
    // split is partial, and that asymmetry is the whole point.
    expect(split.builtin.calls).toBe(140);
  });

  it("reports an entirely untimed side as unmeasured, never as 0ms", () => {
    const split = toolSplit([row("mcp__x__y", 3, null)]);
    expect(split.mcp.ms).toBeNull();
    expect(split.timedMs).toBeNull();
  });

  it("orders the MCP rows by time, longest first", () => {
    expect(toolSplit(REAL.rows).mcpRows.map((r) => r.key)).toEqual([
      "mcp__deepwiki__ask_question",
      "mcp__gitea__Gitea-Hurricane-issue_write",
    ]);
  });

  it("finds the longest average call by derivation, never by a hard-coded name", () => {
    // AskUserQuestion is 16m/call against Bash's 10s, despite Bash holding 5.7x
    // the total. Renaming it must not lose the finding, so nothing here keys on
    // the string.
    expect(toolSplit(REAL.rows).slowest?.key).toBe("AskUserQuestion");
    const renamed = REAL.rows.map((r) =>
      r.key === "AskUserQuestion" ? row("AskAnything", r.tool_calls, r.active_ms ?? null) : r,
    );
    expect(toolSplit(renamed).slowest?.key).toBe("AskAnything");
  });

  it("ignores a row it cannot average when picking the outlier", () => {
    expect(toolSplit([row("Never", 0, 900_000), row("Real", 10, 1000)]).slowest?.key).toBe("Real");
  });
});

describe("UsageToolSplit", () => {
  const html = render(<UsageToolSplit breakdown={REAL} failed={false} />);

  it("states the MCP share of measured tool time", () => {
    // 40,129,873 of 1,326,081,325 measured ms.
    expect(html).toContain("3%");
    expect(html).toContain("MCP servers");
    expect(html).toContain("Built-in");
  });

  it("names which MCP tools cost the time, as identifiers rather than quantities", () => {
    expect(html).toContain("mcp__deepwiki__ask_question");
    expect(html).toContain("font-mono");
  });

  it("says the row set was capped, so a share is not read as the whole truth", () => {
    expect(html).toContain("daemon");
    expect(html).toContain("rather than every tool that ran");
  });

  it("omits the cap sentence when the daemon returned everything", () => {
    const whole = render(
      <UsageToolSplit breakdown={{ ...REAL, truncated: false }} failed={false} />,
    );
    expect(whole).not.toContain("rather than every tool that ran");
  });

  it("names the outlier whose clock is a human waiting", () => {
    expect(html).toContain("Longest average call");
    expect(html).toContain("AskUserQuestion");
    expect(html).toContain("wall clock");
  });

  it("says how many tools were never timed rather than absorbing them silently", () => {
    const partial = render(
      <UsageToolSplit
        breakdown={view([row("Bash", 10, 5000), row("Glob", 40, null)])}
        failed={false}
      />,
    );
    expect(partial).toContain("never timed");
    expect(partial).toContain("is not counted as zero");
  });

  it("keeps an unmeasured figure quiet — never bold, never a zero", () => {
    const untimed = render(
      <UsageToolSplit
        breakdown={view([row("Bash", 10, null)])}
        failed={false}
      />,
    );
    expect(untimed).toContain("not measured");
    expect(untimed).toContain("text-content-tertiary");
    expect(untimed).not.toContain("font-semibold");
    expect(untimed).not.toContain(">0s<");
  });

  it("says so plainly when no MCP server was used at all", () => {
    const none = render(
      <UsageToolSplit
        breakdown={view([row("Bash", 10, 5000)])}
        failed={false}
      />,
    );
    expect(none).toContain("No MCP tool calls in this selection.");
  });

  it("shows the empty copy for a selection with no tool rows", () => {
    const empty = render(
      <UsageToolSplit breakdown={view([])} failed={false} />,
    );
    expect(empty).toContain("Not measured: no tool rows");
  });

  it("shows a skeleton, not the empty copy, while the query is in flight", () => {
    const loading = render(<UsageToolSplit breakdown={undefined} failed={false} />);
    expect(loading).not.toContain("Not measured: no tool rows");
  });

  it("offers the one action that might fix a failed section", () => {
    const broken = render(
      <UsageToolSplit breakdown={undefined} failed onRetry={() => undefined} />,
    );
    expect(broken).toContain("The tool breakdown could not be loaded.");
    expect(broken).toContain('role="alert"');
    expect(broken).toContain("Try again");
  });
});
