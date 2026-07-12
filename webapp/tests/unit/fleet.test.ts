import { describe, it, expect } from "vitest";
import { buildFleetTree, fleetMemberCount } from "@/lib/grove/fleet";
import type { AgentActivityView, SessionActivityView } from "@/lib/grove/types";

function activity(overrides: Partial<AgentActivityView> = {}): AgentActivityView {
  return {
    state: "waiting",
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
    ...overrides,
  };
}

function session(
  id: string,
  parentId: string | null,
  overrides: Partial<AgentActivityView> = {},
): SessionActivityView {
  return {
    session: {
      session_id: id,
      adapter_kind: "claude_code",
      provenance: parentId ? "fs_discovered" : "grove_launched",
      tmux_window: parentId ? null : "agent",
      parent_session_id: parentId,
    },
    activity: activity(overrides),
  };
}

describe("buildFleetTree", () => {
  it("groups a flat sessions list into roots + itemized children by parent_session_id", () => {
    const sessions = [
      session("s1", null),
      session("a1", "s1", { title: "Explore" }),
      session("a2", "s1", { title: "Fix" }),
    ];
    const roots = buildFleetTree(sessions);
    expect(roots).toHaveLength(1);
    expect(roots[0].entry.session.session_id).toBe("s1");
    expect(roots[0].children.map((c) => c.entry.session.session_id)).toEqual(["a1", "a2"]);
    expect(roots[0].children[0].children).toEqual([]);
  });

  it("keeps every top-level session (primary + adopted extras) as its own root", () => {
    const sessions = [session("s1", null), session("s2", null)];
    const roots = buildFleetTree(sessions);
    expect(roots.map((r) => r.entry.session.session_id)).toEqual(["s1", "s2"]);
  });

  it("treats a parent id absent from the same list as a root — defensive against a partial snapshot", () => {
    const sessions = [session("a1", "missing-parent")];
    const roots = buildFleetTree(sessions);
    expect(roots).toHaveLength(1);
    expect(roots[0].entry.session.session_id).toBe("a1");
  });

  it("returns [] for an empty session list — the no-fleet, no-chrome case", () => {
    expect(buildFleetTree([])).toEqual([]);
  });
});

describe("fleetMemberCount", () => {
  it("counts itemized children only, never the roots themselves", () => {
    const roots = buildFleetTree([session("s1", null), session("a1", "s1"), session("a2", "s1")]);
    expect(fleetMemberCount(roots)).toBe(2);
  });

  it("is 0 when nothing has a child — the WorkPanel's tree-vs-count gate", () => {
    const roots = buildFleetTree([session("s1", null)]);
    expect(fleetMemberCount(roots)).toBe(0);
  });
});
