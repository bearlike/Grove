import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { UsageSessions } from "@/components/grove/usage/sessions";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { UsageSessionPageView, UsageSessionRowView } from "@/lib/grove/api";

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
      cache_read: null,
      cache_creation: null,
      reasoning: null,
      output: null,
      provider_total: null,
    },
    subagent_tokens: null,
    duration: { active_ms: 600_000, confidence: "derived" },
    parser_health: "ok",
    ...over,
  } as UsageSessionRowView;
}

function render(session: UsageSessionRowView): string {
  const sessions: UsageSessionPageView = { rows: [session], sort: "recent", coverage: COVERAGE };
  return renderToStaticMarkup(
    <TooltipProvider>
      <UsageSessions sessions={sessions} failed={false} />
    </TooltipProvider>,
  );
}

describe("recent session workspace names", () => {
  it("renders the recorded workspace title beside the linked short session id", () => {
    const html = render(row({ workspace_title: "Keep usage workspace names" }));

    expect(html).toContain('>0197c3f2</a>');
    expect(html).toContain('title="Keep usage workspace names"');
    expect(html).toContain(">Keep usage workspace names</span>");
    expect(html).toMatch(/class="[^"]*truncate[^"]*text-content-tertiary[^"]*"/);
  });

  it("renders a session with no recorded title as its id alone", () => {
    const html = render(row({ workspace_title: null }));

    expect(html).toContain('>0197c3f2</a>');
    expect(html).not.toContain("Untitled");
    expect(html).not.toContain("workspace deleted");
  });

  it("keeps a deleted workspace's recorded name and identifies the deletion", () => {
    const html = render(
      row({
        workspace_title: "Finished workspace",
        workspace_deleted_at: "2026-09-13T00:00:00Z",
      }),
    );

    expect(html).toContain(">Finished workspace</span>");
    expect(html).toContain(">workspace deleted</span>");
    expect(html).toContain("Workspace deleted; this session and its transcript remain available.");
  });
});
