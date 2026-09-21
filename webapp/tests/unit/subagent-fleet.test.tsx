import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { SubagentFleetPanel, fleetRows, fleetSummary } from "@/components/grove/workspace/subagent-fleet";
import type { SubagentFleetData } from "@/lib/grove/hooks/subagent-fleet";

const RUNNING: SubagentFleetData = {
  supported: true,
  error: null,
  session_id: "root",
  subagents: [
    {
      agent_id: "explore-api",
      agent_type: "claude-code",
      state: "working",
      started_at: "2026-09-17T12:00:00Z",
      last_event_at: "2026-09-17T12:01:00Z",
      current_tool: "Read",
      last_message: null,
    },
    {
      agent_id: "check-fixtures",
      agent_type: null,
      state: "waiting",
      started_at: "2026-09-17T12:00:30Z",
      last_event_at: "2026-09-17T12:01:30Z",
      current_tool: null,
      last_message: "Need the fixture source.",
    },
    {
      agent_id: "settled-agent",
      agent_type: "claude-code",
      state: "idle",
      started_at: "2026-09-17T11:00:00Z",
      last_event_at: "2026-09-17T11:01:00Z",
      current_tool: null,
      last_message: "Completed.",
    },
  ],
  sessions: [],
};

describe("fleetRows", () => {
  it("unions hook and session sources rather than joining by lookup", () => {
    // A child fully described only in `sessions` (settled before this card
    // ever read the hook feed) must still appear — the naive
    // `sessions.get(agent_id)` lookup this replaced only ever walked
    // `subagents`, so a session-only row silently never rendered at all.
    const withSessionOnlyChild: SubagentFleetData = {
      ...RUNNING,
      sessions: [
        {
          session: { session_id: "archived-child", adapter_kind: "claude_code", provenance: "subagent", tmux_window: null, parent_session_id: "root" },
          activity: {
            state: "idle",
            title: null,
            current_task: null,
            human_turns: 1,
            assistant_replies: 1,
            replies_per_turn: [],
            tool_calls: 3,
            active_subagents: 0,
            model: null,
            tokens_in: 0,
            tokens_out: 0,
            last_event_at: "2026-09-17T10:00:00Z",
            needs_attention: false,
            error_detail: null,
            questions: [],
          },
        },
      ],
    };

    const rows = fleetRows(withSessionOnlyChild);
    const sessionOnly = rows.find((row) => row.id === "archived-child");

    expect(sessionOnly).toBeTruthy();
    expect(sessionOnly?.sessionId).toBe("archived-child");
    expect(rows).toHaveLength(RUNNING.subagents.length + 1);
  });
});

describe("fleetSummary", () => {
  it("separates live work from attention and settled children", () => {
    expect(fleetSummary(fleetRows(RUNNING))).toEqual({
      running: 1,
      attention: 1,
      settled: 1,
    });
  });
});

describe("SubagentFleetPanel", () => {
  function render(fleet: SubagentFleetData, defaultOpen = false): string {
    return renderToStaticMarkup(
      <SubagentFleetPanel
        fleet={fleet}
        workspaceId="workspace"
        defaultOpen={defaultOpen}
        onOpenTranscript={() => undefined}
      />,
    );
  }

  it("states an empty or unsupported fleet rather than treating either as a failure", () => {
    expect(render({ ...RUNNING, subagents: [] })).toContain("No subagents active");
    expect(render({ ...RUNNING, supported: false, subagents: RUNNING.subagents })).toContain(
      "Subagents unavailable",
    );
  });

  it("renders the live summary while collapsed without mounting row detail", () => {
    const html = render(RUNNING);

    expect(html).toContain('data-testid="subagent-fleet-card"');
    expect(html).toContain("1 running");
    expect(html).toContain("1 awaiting input");
    expect(html).not.toContain("Explore api");
  });

  it("draws a tree of live children before the settled group when open", () => {
    const html = render(RUNNING, true);

    expect(html).toContain("explore api");
    expect(html).toContain("Waiting for you");
    expect(html).toContain("Read");
    expect(html).toContain("Settled 1");
    expect(html.indexOf("explore api")).toBeLessThan(html.indexOf("Settled 1"));
  });

  it("keeps missing provider fields absent instead of fabricating zeros", () => {
    const html = render(RUNNING, true);

    expect(html).not.toContain("0 tokens");
    expect(html).not.toContain("0ms");
    expect(html).not.toContain("Model not reported");
  });

  it("renders a child described only in sessions[], never dropping it for lacking a hook row", () => {
    const withSessionOnlyChild: SubagentFleetData = {
      ...RUNNING,
      subagents: [],
      sessions: [
        {
          session: { session_id: "archived-child", adapter_kind: "claude_code", provenance: "subagent", tmux_window: null, parent_session_id: "root" },
          activity: {
            state: "idle",
            title: null,
            current_task: null,
            human_turns: 1,
            assistant_replies: 1,
            replies_per_turn: [],
            tool_calls: 3,
            active_subagents: 0,
            model: null,
            tokens_in: 0,
            tokens_out: 0,
            last_event_at: "2026-09-17T10:00:00Z",
            needs_attention: false,
            error_detail: null,
            questions: [],
          },
        },
      ],
    };

    // The row itself sits behind the settled group's own disclosure (a second
    // click), which `renderToStaticMarkup` cannot open — so the observable
    // half here is that the child is counted rather than silently vanishing:
    // the naive `sessions.get(agent_id)` lookup this replaced walked only
    // `subagents` and would have rendered the empty state instead.
    const html = render(withSessionOnlyChild, true);

    expect(html).toContain("Settled 1");
    expect(html).not.toContain('data-testid="subagent-fleet-empty"');
  });

  it("renders an error rather than silently treating a refused fleet read as empty", () => {
    const html = render({ ...RUNNING, error: "Fleet snapshot disconnected", subagents: [] });

    expect(html).toContain("Fleet snapshot disconnected");
    expect(html).toContain('data-testid="subagent-fleet-error"');
  });
});
