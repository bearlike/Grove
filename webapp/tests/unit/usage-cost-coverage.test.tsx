import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { UsageBreakdown } from "@/components/grove/usage/breakdown";
import { UsageSessions } from "@/components/grove/usage/sessions";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { UsageBreakdownView, UsageSessionPageView } from "@/lib/grove/api";

const COVERAGE = {
  sources: [],
  degraded_source_count: 0,
  cost_available: false,
  quota_available: false,
};

function render(node: React.ReactNode): string {
  return renderToStaticMarkup(<TooltipProvider>{node}</TooltipProvider>);
}

describe("usage cost cells", () => {
  it("shows the supplied price and provenance in both session and model tables", () => {
    const sessions = {
      sort: "recent",
      coverage: COVERAGE,
      rows: [
        {
          session_id: "session-1",
          provider: "claude_code",
          cwd: null,
          project: null,
          models: [],
          started_at: null,
          last_event_at: null,
          duration: {
            active_ms: null,
            elapsed_span_ms: null,
            confidence: "derived",
          },
          turns: 0,
          tool_calls: 0,
          tool_failures: 0,
          files_changed: 0,
          tokens: {},
          cost: { amount: "3.25", currency: "USD", provenance: "estimated" },
          parser_health: "ok",
        },
      ],
    } as UsageSessionPageView;
    const breakdown = {
      dimension: "model",
      coverage: COVERAGE,
      rows: [
        {
          key: "claude",
          label: "Claude",
          sessions: 1,
          tool_calls: 0,
          tool_failures: 0,
          tokens: {},
          cost: {
            amount: "4.50",
            currency: "USD",
            provenance: "provider_reported",
          },
          latency: { calls: 0, avg_ms: null },
        },
      ],
      truncated: false,
    } as UsageBreakdownView;

    const sessionHtml = render(
      <UsageSessions sessions={sessions} failed={false} />,
    );
    const breakdownHtml = render(
      <UsageBreakdown breakdown={breakdown} failed={false} />,
    );

    expect(sessionHtml).toContain("$3.25");
    expect(sessionHtml).toContain("Estimated");
    expect(breakdownHtml).toContain("$4.50");
    expect(breakdownHtml).toContain("Provider reported");
  });

  it("labels an absent row price unknown instead of fabricating zero", () => {
    const breakdown = {
      dimension: "model",
      coverage: COVERAGE,
      rows: [
        {
          key: "claude",
          label: "Claude",
          sessions: 1,
          tool_calls: 0,
          tool_failures: 0,
          tokens: {},
          cost: null,
          latency: { calls: 0, avg_ms: null },
        },
      ],
      truncated: false,
    } as UsageBreakdownView;

    const html = render(
      <UsageBreakdown breakdown={breakdown} failed={false} />,
    );

    expect(html).toContain(">unknown<");
    expect(html).not.toContain("$0.00");
  });
});
