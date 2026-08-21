import { describe, expect, it, vi } from "vitest";

import { submitLaunch } from "@/lib/grove/runtime/launch";
import type { LaunchState, LaunchValues } from "@/components/grove/launch/launch-state";
import type { CreateWorkspaceRequest, WorkspaceStateView } from "@/lib/grove/api";

const values: LaunchValues = {
  repoRoot: "/repos/grove",
  projectCwd: "/repos/grove/webapp",
  selectedProjectCwd: "/repos/grove/webapp",
  agentName: "claude",
  model: "sonnet",
  customModel: false,
  runtime: "container",
  brief: true,
  branchMode: "auto",
  branchName: "",
  existingBranch: "",
  remoteRef: "",
  localName: "",
  baseRef: "main",
  skipInit: false,
  titleOverride: null,
  ticket: null,
};

const state: LaunchState = { values, touched: new Set() };

describe("submitLaunch", () => {
  it("builds the sole create request before navigating to the new workspace", async () => {
    const workspace = { id: "workspace-1" } as WorkspaceStateView;
    const create = vi.fn<(request: CreateWorkspaceRequest) => Promise<WorkspaceStateView>>().mockResolvedValue(workspace);
    const navigate = vi.fn<(href: string) => void>();

    const restore = vi.fn<(prompt: string) => void>();

    await submitLaunch(state, "Create the composer\nwith defaults", create, navigate, restore);

    expect(create).toHaveBeenCalledWith({
      repo_root: "/repos/grove",
      project_cwd: "/repos/grove/webapp",
      agent_name: "claude",
      title: expect.stringMatching(/^[0-9a-f]{10}$/),
      initial_prompt: "Create the composer\nwith defaults",
      // Untouched pills still contribute nothing — except branch_plan, which
      // must carry what the pill shows because the request's own default does
      // not consult the defaults cascade.
      branch_plan: { kind: "auto", base_ref: "main" },
    });
    expect(navigate).toHaveBeenCalledWith("/w/workspace-1");
    expect(restore).not.toHaveBeenCalled();
  });

  it("restores the prompt and preserves a rejected create without navigating", async () => {
    const refusal = new Error("branch already exists");
    const create = vi.fn<(request: CreateWorkspaceRequest) => Promise<WorkspaceStateView>>().mockRejectedValue(refusal);
    const navigate = vi.fn<(href: string) => void>();
    const restore = vi.fn<(prompt: string) => void>();

    await expect(submitLaunch(state, "Retry this task", create, navigate, restore)).rejects.toBe(refusal);

    expect(restore).toHaveBeenCalledWith("Retry this task");
    expect(navigate).not.toHaveBeenCalled();
  });
});
