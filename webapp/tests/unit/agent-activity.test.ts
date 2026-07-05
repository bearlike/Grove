import { describe, expect, it } from "vitest";
import { AgentLiveStatus } from "@/lib/grove/agent-activity";
import type { AgentActivityView } from "@/lib/grove/types";

function activity(patch: Partial<AgentActivityView> = {}): AgentActivityView {
  return {
    state: "working",
    title: null,
    current_task: null,
    human_turns: 0,
    assistant_replies: 0,
    replies_per_turn: [],
    tool_calls: 0,
    active_subagents: 0,
    model: null,
    tokens_in: 0,
    tokens_out: 0,
    last_event_at: null,
    needs_attention: false,
    error_detail: null,
    questions: [],
    interpreted_status: null,
    ...patch,
  };
}

describe("AgentLiveStatus", () => {
  it("models a sessionless workspace as neutral, never undefined", () => {
    const s = AgentLiveStatus.of(null);
    expect(s.hasSession).toBe(false);
    expect(s.state).toBe("unknown");
    expect(s.taskLine).toBeNull();
    expect(s.metricsLine).toBeNull();
    expect(s.subagents).toBe(0);
    expect(s.errorDetail).toBeNull();
  });

  it("happening-now precedence: current_task ▸ interpreted_status ▸ title", () => {
    expect(
      AgentLiveStatus.of(
        activity({ current_task: "running tests", interpreted_status: "busy", title: "fix bug" }),
      ).taskLine,
    ).toBe("running tests");
    expect(
      AgentLiveStatus.of(activity({ current_task: null, interpreted_status: "busy", title: "fix bug" }))
        .taskLine,
    ).toBe("busy");
    expect(
      AgentLiveStatus.of(activity({ current_task: null, interpreted_status: null, title: "fix bug" }))
        .taskLine,
    ).toBe("fix bug");
  });

  it("surfaces error detail only while in the error state", () => {
    expect(AgentLiveStatus.of(activity({ state: "error", error_detail: "boom" })).errorDetail).toBe(
      "boom",
    );
    // A stale error_detail on a non-error state must not leak into the slot.
    expect(
      AgentLiveStatus.of(activity({ state: "working", error_detail: "boom" })).errorDetail,
    ).toBeNull();
  });

  it("formats the muted metrics one-liner", () => {
    expect(
      AgentLiveStatus.of(
        activity({ human_turns: 3, tool_calls: 12, tokens_in: 188000, tokens_out: 12400 }),
      ).metricsLine,
    ).toBe("3t · 12⚒ · 188.0k↑ 12.4k↓");
  });

  it("subagents fall back to 0 for a pre-field payload", () => {
    const a = activity({ active_subagents: 2 });
    expect(AgentLiveStatus.of(a).subagents).toBe(2);
    // Simulate a daemon that never sent the field.
    delete (a as Partial<AgentActivityView>).active_subagents;
    expect(AgentLiveStatus.of(a).subagents).toBe(0);
  });
});
