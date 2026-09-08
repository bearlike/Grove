import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { UsageQuota } from "@/components/grove/usage/quota";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { UsageQuotasView } from "@/lib/grove/api";

const QUOTAS: UsageQuotasView = {
  accounts: [
    {
      account_id: "claude_code-primary",
      provider: "claude_code",
      label: "Personal Claude",
      billing_mode: "subscription",
      subscription: { plan: "max", label: "Max", detail: "20x" },
      status: "ok",
      detail: null,
      windows: [
        {
          scope: "session",
          label: "session",
          window_seconds: null,
          used_percent: 24,
          remaining_percent: 76,
          resets_at: "2026-09-07T12:00:00Z",
          limit: null,
          used: null,
          unit: null,
          observed_at: "2026-09-06T12:00:00Z",
          evidence: "provider_endpoint",
          projection: {
            elapsed_percent: null,
            resolved_window_seconds: 18_000,
            burn_rate: null,
            projected_percent: null,
            verdict: "unknown",
            exhausts_at: null,
            tokens_used: null,
            tokens_available_estimate: null,
          },
        },
      ],
      spend: null,
      observed_at: "2026-09-06T12:00:00Z",
      stale_seconds: null,
    },
  ],
  coverage: {
    sources: [],
    degraded_source_count: 0,
    cost_available: false,
    quota_available: true,
  },
};

function render(): string {
  return renderToStaticMarkup(
    <TooltipProvider>
      <UsageQuota quotas={QUOTAS} failed={false} />
    </TooltipProvider>,
  );
}

describe("subscription quota structure", () => {
  it("keeps account identity in the shared card header", () => {
    const html = render();

    expect(html).toMatch(/data-testid="usage-quota-card"[\s\S]*?Personal Claude[\s\S]*?Claude code · Max 20x/);
    expect(html).toContain('data-slot="badge"');
  });

  it("separates the scanable window readings from their labeled metrics", () => {
    const html = render();

    expect(html).toMatch(/aria-label="Windows"[\s\S]*?>Windows</);
    expect(html).toMatch(/aria-label="Window metrics"[\s\S]*?>Metrics</);
    expect(html).toMatch(
      /class="[^\"]*\bborder-t\b[^\"]*\bborder-border\b[^\"]*" aria-label="Window metrics"/,
    );
  });

  it("uses container breakpoints and keeps every card shrinkable", () => {
    const html = render();

    expect(html).toContain('@container');
    expect(html).toContain('@3xl:grid-cols-2');
    expect(html).not.toContain('min-w-[');
  });

  it("keeps the metric identity yielding beside its controls", () => {
    const html = render();

    expect(html).toContain('grid-cols-[minmax(0,1fr)_auto]');
    expect(html).toContain('class="flex min-w-0 items-center gap-1.5"');
  });
});
