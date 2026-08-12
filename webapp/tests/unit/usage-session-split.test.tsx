import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { UsageSessions, delegatedPercent } from "@/components/grove/usage/sessions";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { UsageSessionPageView, UsageSessionRowView } from "@/lib/grove/api";

/**
 * The two splits the audit table publishes: where a session's TIME went, and
 * how much of its TOKEN spend it delegated.
 *
 * Both are partitions of a number already in the table, so the failure worth
 * pinning is not a wrong total but a fabricated part — a `0%` for a share
 * nobody measured, or a `0s` for a tool time that was never timed. Those read
 * as measurements, which is exactly what the wire's nullability exists to
 * prevent, and neither is visible in the happy path.
 */

const COVERAGE = {
  sources: [],
  degraded_source_count: 0,
  cost_available: false,
  quota_available: false,
};

function row(over: Partial<UsageSessionRowView> = {}): UsageSessionRowView {
  return {
    session_id: "0197c3f2-aaaa-bbbb-cccc-ddddeeeeffff",
    provider: "claude_code",
    cwd: "/repo",
    project: "/repo",
    models: [],
    turns: 12,
    tool_calls: 40,
    tool_failures: 0,
    files_changed: 3,
    tokens: {
      fresh_input: 1_000_000,
      cache_read: 3_000_000,
      cache_creation: null,
      reasoning: null,
      output: null,
      provider_total: null,
    },
    subagent_tokens: null,
    duration: {
      active_ms: 600_000,
      execution_ms: 1_800_000,
      generation_ms: 1_200_000,
      tool_ms: 600_000,
      elapsed_span_ms: 900_000,
      confidence: "derived",
    },
    parser_health: "ok",
    ...over,
  } as UsageSessionRowView;
}

function render(rows: UsageSessionRowView[]): string {
  const page: UsageSessionPageView = { rows, sort: "recent", coverage: COVERAGE } as never;
  return renderToStaticMarkup(
    <TooltipProvider>
      <UsageSessions sessions={page} failed={false} />
    </TooltipProvider>,
  );
}

describe("delegatedPercent", () => {
  it("is the sub-agent share of the total the Tokens column already shows", () => {
    // `tokens` includes the delegated work, so the share is a fraction of it.
    const measured = row({
      tokens: {
        fresh_input: 8_000_000,
        cache_read: 2_000_000,
        cache_creation: null,
        reasoning: null,
        output: null,
        provider_total: null,
      },
      subagent_tokens: {
        fresh_input: 2_500_000,
        cache_read: null,
        cache_creation: null,
        reasoning: null,
        output: null,
        provider_total: null,
      },
    });

    expect(delegatedPercent(measured)).toBeCloseTo(25);
  });

  it("is null when no sub-agent usage was measured — never a share of zero", () => {
    expect(delegatedPercent(row({ subagent_tokens: null }))).toBeNull();
  });

  it("is null when the session's own total was not measured, so there is no denominator", () => {
    const noTotal = row({
      tokens: {
        fresh_input: null,
        cache_read: null,
        cache_creation: null,
        reasoning: null,
        output: null,
        provider_total: null,
      },
      subagent_tokens: {
        fresh_input: 500,
        cache_read: null,
        cache_creation: null,
        reasoning: null,
        output: null,
        provider_total: null,
      },
    });

    expect(delegatedPercent(noTotal)).toBeNull();
  });
});

describe("the delegated column", () => {
  it("renders a dash and says why, rather than 0%", () => {
    const html = render([row({ subagent_tokens: null })]);

    expect(html).not.toContain("0%");
    expect(html).toContain("No sub-agent usage measured for this session");
  });

  it("renders a real but tiny share as `<1%`, the one value that would round to 0%", () => {
    const tiny = row({
      tokens: {
        fresh_input: 10_000_000,
        cache_read: null,
        cache_creation: null,
        reasoning: null,
        output: null,
        provider_total: null,
      },
      subagent_tokens: {
        fresh_input: 20_000,
        cache_read: null,
        cache_creation: null,
        reasoning: null,
        output: null,
        provider_total: null,
      },
    });

    const html = render([tiny]);

    expect(html).toContain("&lt;1%");
    expect(html).not.toContain(">0%<");
  });
});

describe("the time columns", () => {
  it("shows the two halves of compute time rather than the total they add to", () => {
    const html = render([row()]);

    // 20m of model wait plus 10m of tool time — the 30m total is deliberately
    // not its own column, since a reader can add and the parts cannot be
    // recovered from it.
    expect(html).toContain("Model wait");
    expect(html).toContain("Tool time");
    expect(html).toContain("20m");
    expect(html).toContain("10m");
    // The wall clock stays: it is the OTHER reducer, not part of this split.
    expect(html).toContain("Wall clock");
  });

  it("says not measured for a half nothing timed, never 0s", () => {
    const noTools = row({
      duration: {
        active_ms: 600_000,
        execution_ms: 1_200_000,
        generation_ms: 1_200_000,
        tool_ms: null,
        elapsed_span_ms: 900_000,
        confidence: "derived",
      },
    });

    const html = render([noTools]);

    expect(html).toContain("not measured");
    expect(html).not.toContain(">0s<");
  });
});
