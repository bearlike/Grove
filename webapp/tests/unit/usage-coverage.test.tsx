import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { UsageCoverage } from "@/components/grove/usage/coverage";
import { groveKeys } from "@/lib/grove/hooks";
import type { UsageCoverageView, WhoamiView } from "@/lib/grove/api";

/**
 * The one requirement that matters here: a Langfuse button is worse than no
 * button when it points at a host nobody actually finished configuring, so
 * this pins the daemon's `langfuse_host` gate as the SOLE switch — not a
 * second client-side opinion about what "configured" means.
 */

const COVERAGE: UsageCoverageView = {
  sources: [],
  degraded_source_count: 0,
  cost_available: false,
  quota_available: true,
  last_refresh_at: "2026-08-11T09:00:00Z",
  earliest_event_at: "2026-01-01T00:00:00Z",
  latest_event_at: "2026-08-11T09:00:00Z",
};

function identity(langfuse_host: string | null): WhoamiView {
  return {
    version: "0.4.2",
    started_at: "2026-08-11T08:00:00Z",
    uptime_seconds: 3600,
    host: "example-host",
    user: "example-user",
    platform: "linux",
    python_version: "3.13.0",
    latest_version: null,
    update_available: false,
    langfuse_host,
  };
}

function render(langfuse_host: string | null, coverage: UsageCoverageView = COVERAGE): string {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(groveKeys.whoami, identity(langfuse_host));
  return renderToStaticMarkup(
    <QueryClientProvider client={client}>
      <UsageCoverage
        coverage={coverage}
        failed={false}
        onRefresh={() => {}}
        refreshing={false}
        note={null}
      />
    </QueryClientProvider>,
  );
}

describe("UsageCoverage — the Langfuse button", () => {
  it("renders nothing when the daemon reports no configured host", () => {
    const html = render(null);
    expect(html).not.toContain("Open in Langfuse");
    expect(html).not.toContain("langfuse-mark");
  });

  it("renders once the daemon reports a fully-configured host", () => {
    const html = render("https://cloud.langfuse.example");
    expect(html).toContain("Open in Langfuse");
    expect(html).toContain('href="https://cloud.langfuse.example"');
    // Opens in a new tab rather than navigating the dashboard away.
    expect(html).toContain('target="_blank"');
    expect(html).toContain('data-testid="langfuse-mark"');
  });

  it("always gives the Refresh button its own icon", () => {
    // Item 4 of the ticket: Refresh had no icon at all before this change.
    const html = render(null);
    const refreshButton = html.slice(html.indexOf(">Refresh<") - 400, html.indexOf(">Refresh<"));
    expect(refreshButton).toContain("lucide-refresh-cw");
  });
});

/**
 * An unindexed store and a store holding genuinely no usage render every figure
 * on the page identically, and only one of them has a remedy. A schema-version
 * bump discards the derived cache with nothing to reindex it, so this is the
 * ordinary way a real user meets an empty usage page.
 */
describe("UsageCoverage — indexed versus never indexed", () => {
  const NEVER_INDEXED: UsageCoverageView = {
    ...COVERAGE,
    last_refresh_at: null,
    earliest_event_at: null,
    latest_event_at: null,
  };

  it("says the index is missing, and names Refresh as the remedy", () => {
    const html = render(null, NEVER_INDEXED);

    expect(html).toContain('data-testid="coverage-unindexed"');
    expect(html).not.toContain('data-testid="coverage-empty"');
  });

  it("does not DATE a page that rests on nothing", () => {
    // The header read "Indexed — · events from — to —": three em-dashes in a
    // sentence whose grammar still claims an index exists.
    const html = render(null, NEVER_INDEXED);

    expect(html).toContain("Not indexed yet");
    expect(html).not.toContain("events from");
  });

  it("distinguishes a completed index that found nothing", () => {
    // `last_refresh_at` is the discriminator, not the source count: a finished
    // index still stamps a time, so telling this user to press Refresh would
    // promise a change that cannot happen.
    const html = render(null, { ...COVERAGE, sources: [] });

    expect(html).toContain('data-testid="coverage-empty"');
    expect(html).not.toContain('data-testid="coverage-unindexed"');
  });

  it("says neither thing once sources are actually indexed", () => {
    const html = render(null, {
      ...COVERAGE,
      sources: [
        {
          source_id: "claude",
          provider: "claude_code",
          label: ".claude",
          health: "ok",
          detail: null,
          session_count: 12,
        },
      ],
    });

    expect(html).not.toContain('data-testid="coverage-unindexed"');
    expect(html).not.toContain('data-testid="coverage-empty"');
  });
});
