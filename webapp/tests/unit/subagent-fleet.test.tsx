import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  SubagentFleetPanel,
  fleetRows,
  fleetSummary,
  subagentRun,
} from "@/components/grove/workspace/subagent-fleet";
import type { SubagentFleetData } from "@/lib/grove/hooks/subagent-fleet";

type Child = SubagentFleetData["sessions"][number];
type AgentState = Child["activity"]["state"];

/** One transcript-backed child, the shape the fleet route actually serves. */
function child(
  id: string,
  state: AgentState,
  lastEventAt: string,
  extra: Partial<Child["activity"]> = {},
): Child {
  return {
    session: { session_id: id, adapter_kind: "claude_code", provenance: "fs_discovered", tmux_window: null, parent_session_id: "root" },
    activity: {
      state,
      title: null,
      current_task: null,
      human_turns: 1,
      assistant_replies: 1,
      replies_per_turn: [1],
      tool_calls: 0,
      active_subagents: 0,
      model: null,
      tokens_in: 0,
      tokens_out: 0,
      last_event_at: lastEventAt,
      started_at: "2026-09-17T09:00:00Z",
      needs_attention: false,
      error_detail: null,
      questions: [],
      ...extra,
    },
  };
}

function fleet(sessions: Child[], subagents: SubagentFleetData["subagents"] = []): SubagentFleetData {
  return { supported: true, error: null, session_id: "root", subagents, sessions };
}

describe("subagentRun", () => {
  it("never reports a subagent as waiting for a person: a closed turn is a finished run", () => {
    // `waiting` is the root axis's "waiting for you"; a child's closed turn
    // means it handed its result back, so it must read as finished.
    expect(subagentRun("waiting", true)).toBe("finished");
    expect(subagentRun("blocked", true)).toBe("finished");
    expect(subagentRun("idle", null)).toBe("finished");
  });

  it("marks a child the parent has stopped waiting on as stopped, not running", () => {
    // A child killed mid-tool keeps reading `working` forever; only the root
    // having closed its turn can say so. Both answers are asserted, so the
    // guard cannot pass by always returning one of them.
    expect(subagentRun("working", true)).toBe("running");
    expect(subagentRun("working", false)).toBe("stopped");
    expect(subagentRun("starting", false)).toBe("stopped");
  });

  it("trusts the child's own claim while the root's state is unknown", () => {
    expect(subagentRun("working", null)).toBe("running");
  });

  it("treats an errored child as stopped", () => {
    expect(subagentRun("error", true)).toBe("stopped");
  });
});

describe("fleetRows", () => {
  it("puts running children first, then everything else newest first", () => {
    const rows = fleetRows(
      fleet([
        child("old-finished", "waiting", "2026-09-17T10:00:00Z"),
        child("running-older", "working", "2026-09-17T10:05:00Z"),
        child("new-finished", "idle", "2026-09-17T10:30:00Z"),
        child("running-newer", "working", "2026-09-17T10:20:00Z"),
      ]),
      true,
    );

    // A running child that is OLDER than a finished one still leads, which is
    // what separates run-first from plain recency ordering.
    expect(rows.map((row) => row.id)).toEqual(["running-newer", "running-older", "new-finished", "old-finished"]);
  });

  it("sinks a row with no recorded activity below every row that has one", () => {
    const rows = fleetRows(
      fleet([child("no-clock", "idle", "not-a-date"), child("dated", "idle", "2026-09-17T10:00:00Z")]),
      true,
    );
    expect(rows.map((row) => row.id)).toEqual(["dated", "no-clock"]);
  });

  it("unions hook and session sources rather than joining by lookup", () => {
    // A child fully described only in `sessions` (settled before this card
    // ever read the hook feed) must still appear.
    const rows = fleetRows(
      fleet(
        [child("archived-child", "idle", "2026-09-17T10:00:00Z")],
        [
          {
            agent_id: "hook-only",
            agent_type: "Explore",
            state: "working",
            started_at: "2026-09-17T12:00:00Z",
            last_event_at: "2026-09-17T12:01:00Z",
            current_tool: "Read",
            last_message: null,
          },
        ],
      ),
    );

    expect(rows.map((row) => row.id).sort()).toEqual(["archived-child", "hook-only"]);
    expect(rows.find((row) => row.id === "archived-child")?.sessionId).toBe("archived-child");
    expect(rows.find((row) => row.id === "hook-only")?.sessionId).toBeNull();
  });

  it("carries the child's start instant, distinct from its last activity", () => {
    const [row] = fleetRows(fleet([child("c", "idle", "2026-09-17T10:30:00Z")]));
    expect(row.startedAt).toBe("2026-09-17T09:00:00Z");
    expect(row.lastEventAt).toBe("2026-09-17T10:30:00Z");
  });
});

describe("fleetSummary", () => {
  it("counts runs, not attention", () => {
    const rows = fleetRows(
      fleet([
        child("a", "working", "2026-09-17T10:00:00Z"),
        child("b", "waiting", "2026-09-17T10:00:00Z"),
        child("c", "error", "2026-09-17T10:00:00Z"),
      ]),
      true,
    );
    expect(fleetSummary(rows)).toEqual({ running: 1, finished: 1, stopped: 1 });
  });
});

describe("SubagentFleetPanel", () => {
  function render(data: SubagentFleetData, parentWorking: boolean | null = true, defaultOpen = true): string {
    return renderToStaticMarkup(
      <SubagentFleetPanel
        fleet={data}
        workspaceId="workspace"
        parentWorking={parentWorking}
        defaultOpen={defaultOpen}
        onOpenTranscript={() => undefined}
      />,
    );
  }

  const described = fleet([
    child("aada339568082819e", "waiting", "2026-09-17T10:00:00Z", { current_task: "Map border regression tests" }),
  ]);

  it("titles a row by its description and demotes the id below it", () => {
    const html = render(described);

    const title = html.indexOf('data-testid="subagent-title"');
    const id = html.indexOf('data-testid="subagent-id"');
    expect(html.slice(title, title + 120)).toContain("Map border regression tests");
    expect(html.slice(id, id + 120)).toContain("aada339568082819e");
    expect(title).toBeLessThan(id);
  });

  it("titles a row by its id when no description was recorded, without printing it twice", () => {
    const html = render(fleet([child("lonely-id", "idle", "2026-09-17T10:00:00Z")]));

    expect(html).toContain("lonely-id");
    expect(html).not.toContain('data-testid="subagent-id"');
  });

  it("never renders the agent axis's attention vocabulary for a subagent", () => {
    const html = render(described);

    expect(html).not.toContain("Waiting for you");
    expect(html).not.toContain("awaiting input");
    expect(html).not.toContain('data-testid="agent-state-badge"');
    expect(html).toContain('data-run="finished"');
  });

  it("spends no destructive tone on any run state", () => {
    const html = render(
      fleet([
        child("r", "working", "2026-09-17T10:00:00Z"),
        child("f", "waiting", "2026-09-17T10:00:00Z"),
        child("s", "error", "2026-09-17T10:00:00Z"),
      ]),
    );

    expect(html).toContain('data-run="running"');
    expect(html).toContain('data-run="finished"');
    expect(html).toContain('data-run="stopped"');
    expect(html).not.toContain('data-variant="destructive"');
  });

  it("states when each child started and when it was last active", () => {
    const html = render(described);

    expect(html).toContain('data-testid="subagent-started"');
    expect(html).toContain('data-testid="subagent-last-active"');
    expect(html).toContain('dateTime="2026-09-17T09:00:00Z"');
    expect(html).toContain('dateTime="2026-09-17T10:00:00Z"');
  });

  it("omits the start line for a child whose adapter did not measure one", () => {
    const html = render(
      fleet([child("c", "idle", "2026-09-17T10:00:00Z", { started_at: null })]),
    );

    expect(html).not.toContain('data-testid="subagent-started"');
    expect(html).toContain('data-testid="subagent-last-active"');
  });

  it("summarises runs in the collapsed header without mounting row detail", () => {
    const html = render(
      fleet([child("r", "working", "2026-09-17T10:00:00Z"), child("f", "waiting", "2026-09-17T10:00:00Z")]),
      true,
      false,
    );

    expect(html).toContain('data-testid="subagent-fleet-card"');
    expect(html).toContain("1 running · 1 finished");
    expect(html).not.toContain('data-testid="subagent-fleet-member"');
  });

  it("renders every child in one list, running first, with no second disclosure", () => {
    const html = render(
      fleet([child("finished-one", "idle", "2026-09-17T11:00:00Z"), child("running-one", "working", "2026-09-17T10:00:00Z")]),
    );

    expect(html).not.toContain("Settled");
    expect(html.indexOf("running-one")).toBeLessThan(html.indexOf("finished-one"));
  });

  it("states an empty or unsupported fleet rather than treating either as a failure", () => {
    expect(render(fleet([]))).toContain('data-testid="subagent-fleet-empty"');
    expect(render({ ...described, supported: false })).toContain("Subagents unavailable");
  });

  it("renders an error rather than silently treating a refused fleet read as empty", () => {
    const html = render({ ...fleet([]), error: "Fleet snapshot disconnected" });

    expect(html).toContain("Fleet snapshot disconnected");
    expect(html).toContain('data-testid="subagent-fleet-error"');
  });
});
