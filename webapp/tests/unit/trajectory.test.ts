import { describe, expect, it } from "vitest";

import {
  buildTrajectory,
  formatSpanDuration,
  timelineTicks,
  type TrajectoryInput,
} from "@/lib/grove/adapters/trajectory";
import type { DigestEntryView, SessionTurnView, SubagentFleetView } from "@/lib/grove/api";

const T0 = Date.parse("2026-09-24T10:00:00Z");
const iso = (seconds: number) => new Date(T0 + seconds * 1000).toISOString();

function call(
  id: string,
  at: number,
  status: "ok" | "error" | "running",
  duration: number | null,
  name = "Bash",
): DigestEntryView {
  return {
    role: "tool",
    text: name,
    at: iso(at),
    tool: {
      name,
      tool_use_id: id,
      status,
      input: { command: "npm test" },
      result: null,
      duration_ms: duration,
      body: "inline",
    },
  };
}

function turn(prompt: string, at: number, entries: DigestEntryView[]): SessionTurnView {
  return { user_text: prompt, started_at: iso(at), entries };
}

function input(overrides: Partial<TrajectoryInput>): TrajectoryInput {
  return {
    sessionId: "root",
    turns: [],
    firstTurnIndex: 0,
    rootState: "waiting",
    fleet: null,
    childTurns: new Map(),
    ...overrides,
  };
}

function child(
  sessionId: string,
  state: string,
  startedAt: number,
  spawn: string | null,
): SubagentFleetView["sessions"][number] {
  return {
    session: {
      session_id: sessionId,
      adapter_kind: "claude_code",
      provenance: "fs_discovered",
      tmux_window: null,
      parent_session_id: "root",
      spawn_tool_use_id: spawn,
    },
    activity: {
      state,
      title: "Explore",
      current_task: "Survey the auth flow",
      started_at: iso(startedAt),
      last_event_at: iso(startedAt + 5),
    },
  } as unknown as SubagentFleetView["sessions"][number];
}

describe("buildTrajectory", () => {
  it("roots each turn and nests its steps with their own clocks", () => {
    const { spans } = buildTrajectory(
      input({
        turns: [
          turn("Add a test", 0, [
            { role: "assistant", text: "On it.\nMore detail", at: iso(1) },
            call("t1", 2, "ok", 3000),
          ]),
        ],
      }),
    );
    const [root, message, bash] = spans;
    expect(root).toMatchObject({ id: "t:0", parentSpanId: null, type: "turn", status: "completed" });
    expect(message).toMatchObject({ parentSpanId: "t:0", type: "message", name: "On it.", startedAt: T0 + 1000 });
    expect(bash).toMatchObject({
      id: "c:root:t1",
      parentSpanId: "t:0",
      type: "tool",
      status: "completed",
      startedAt: T0 + 2000,
      endedAt: T0 + 5000,
      latencyMs: 3000,
    });
    // The turn spans exactly as far as its latest step.
    expect(root.endedAt).toBe(T0 + 5000);
  });

  it("maps every status: running, completed, failed and skipped", () => {
    const turns = [
      turn("first", 0, [call("old", 1, "running", null), call("next", 2, "ok", 1000)]),
      turn("second", 10, [call("bad", 11, "error", 500), call("live", 12, "running", null)]),
    ];
    const { spans, counts } = buildTrajectory(input({ turns, rootState: "working" }));
    const status = Object.fromEntries(spans.map((s) => [s.id, s.status]));
    // An unresolved call in an EARLIER turn was abandoned when the next prompt
    // arrived — it never ran to completion, and it is not still running.
    expect(status["c:root:old"]).toBe("skipped");
    expect(status["c:root:bad"]).toBe("failed");
    expect(status["c:root:live"]).toBe("running");
    expect(status["t:1"]).toBe("running");
    expect(counts).toEqual({ running: 1, completed: 1, failed: 1, skipped: 1 });
  });

  it("stops the tail's unresolved call once the root has closed its turn", () => {
    const { spans } = buildTrajectory(
      input({ turns: [turn("go", 0, [call("hung", 1, "running", null)])], rootState: "idle" }),
    );
    expect(spans.find((s) => s.id === "c:root:hung")?.status).toBe("skipped");
    expect(spans.find((s) => s.id === "t:0")?.status).toBe("completed");
  });

  it("gives an abandoned call the turn's extent rather than zero time", () => {
    const { spans } = buildTrajectory(
      input({
        turns: [turn("go", 0, [call("hung", 1, "running", null), call("after", 2, "ok", 4000)])],
        rootState: "idle",
      }),
    );
    const hung = spans.find((s) => s.id === "c:root:hung")!;
    expect(hung.endedAt).toBe(T0 + 6000);
  });

  it("nests a sub-agent under the exact call that spawned it, with its own steps", () => {
    const turns = [turn("fan out", 0, [call("spawn", 1, "ok", 9000, "Agent")])];
    const childTurns = new Map([["kid", [turn("Survey", 2, [call("k1", 3, "ok", 1000, "Read")])]]]);
    const fleet = { sessions: [child("kid", "waiting", 2, "spawn")], subagents: [], supported: true } as unknown as SubagentFleetView;
    const { spans } = buildTrajectory(input({ turns, fleet, childTurns }));
    expect(spans.find((s) => s.id === "a:kid")).toMatchObject({
      parentSpanId: "c:root:spawn",
      type: "agent",
      status: "completed",
      name: "Survey the auth flow",
    });
    expect(spans.find((s) => s.id === "c:kid:k1")).toMatchObject({ parentSpanId: "a:kid", status: "completed" });
  });

  it("falls back to the turn a child started in when no spawn link was recorded", () => {
    const turns = [turn("one", 0, []), turn("two", 10, [])];
    const fleet = { sessions: [child("kid", "working", 12, null)], subagents: [], supported: true } as unknown as SubagentFleetView;
    const { spans } = buildTrajectory(input({ turns, fleet, rootState: "working" }));
    expect(spans.find((s) => s.id === "a:kid")).toMatchObject({ parentSpanId: "t:1", status: "running", endedAt: null });
  });

  it("reads a child still claiming work under a settled root as skipped, never running", () => {
    const turns = [turn("one", 0, [])];
    const fleet = { sessions: [child("kid", "working", 1, null)], subagents: [], supported: true } as unknown as SubagentFleetView;
    const { spans } = buildTrajectory(input({ turns, fleet, rootState: "waiting" }));
    expect(spans.find((s) => s.id === "a:kid")?.status).toBe("skipped");
  });

  it("keeps span ids stable when an earlier window is prepended", () => {
    const later = turn("later", 60, [call("x", 61, "ok", 10)]);
    const narrow = buildTrajectory(input({ turns: [later], firstTurnIndex: 5 }));
    const wide = buildTrajectory(input({ turns: [turn("earlier", 0, []), later], firstTurnIndex: 4 }));
    expect(narrow.spans.map((s) => s.id)).toEqual(["t:5", "c:root:x"]);
    expect(wide.spans.map((s) => s.id)).toContain("t:5");
  });

  it("counts an unstamped turn instead of inventing a time for it", () => {
    const untimed: SessionTurnView = { user_text: "?", started_at: null, entries: [{ role: "assistant", text: "hi" }] };
    const result = buildTrajectory(input({ turns: [untimed] }));
    expect(result.spans).toEqual([]);
    expect(result.untimed).toBe(1);
  });
});

describe("formatSpanDuration", () => {
  it("steps up units as a reader compares them", () => {
    expect(formatSpanDuration(null)).toBeNull();
    expect(formatSpanDuration(0)).toBe("0s");
    expect(formatSpanDuration(400)).toBe("<1s");
    expect(formatSpanDuration(42_000)).toBe("42s");
    expect(formatSpanDuration(125_000)).toBe("2m 5s");
    expect(formatSpanDuration(3_725_000)).toBe("1h 2m");
  });
});

describe("timelineTicks", () => {
  it("chooses a round step within the limit", () => {
    const ticks = timelineTicks({ min: 0, max: 150_000 }, 5);
    // 150s over five steps: 30s is the smallest round step that fits.
    expect(ticks.map((t) => t.label)).toEqual(["0", "30s", "1m", "1m 30s", "2m", "2m 30s"]);
    expect(timelineTicks({ min: 0, max: 150_000 }, 3).map((t) => t.label)).toEqual(["0", "1m", "2m"]);
    expect(ticks[0]!.percent).toBe(0);
  });
});
