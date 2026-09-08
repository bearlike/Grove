import { describe, expect, it } from "vitest";

import { agentExited } from "@/components/grove/workspace/selectors";
import type { WorkspaceActivityView } from "@/lib/grove/api";

function activity(state: string, currentTask: string | null): WorkspaceActivityView {
  return {
    sessions: [{ activity: { state, current_task: currentTask } }],
  } as unknown as WorkspaceActivityView;
}

describe("agentExited", () => {
  it("returns the recorded exit reason only for an errored primary session", () => {
    expect(agentExited(activity("error", "agent exited with status 1"))).toBe(
      "agent exited with status 1",
    );
    expect(agentExited(activity("working", "agent exited with status 1"))).toBeNull();
    expect(agentExited(activity("error", "  "))).toBeNull();
    expect(agentExited(activity("error", null))).toBeNull();
    expect(agentExited(null)).toBeNull();
  });
});
