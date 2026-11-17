import { describe, expect, it, vi } from "vitest";

import { submitLaunch } from "@/lib/grove/runtime/launch";
import type { LaunchState, LaunchValues } from "@/components/grove/launch/launch-state";
import type { CreateWorkspaceRequest, WorkspaceStateView } from "@/lib/grove/api";

const values: LaunchValues = {
  repoRoot: "/repos/grove",
  projectCwd: "/repos/grove/webapp",
  selectedProjectCwd: "/repos/grove/webapp",
  agentName: "claude",
  agentKind: null,
  model: "sonnet",
  customModel: false,
  effort: null,
  runtime: "container",
  node: null,
  brief: true,
  native: null,
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

const state: LaunchState = { values, touched: new Set(), seededRepo: values.repoRoot };

describe("submitLaunch", () => {
  it("builds the sole create request before navigating to the new workspace", async () => {
    const workspace = { id: "workspace-1" } as WorkspaceStateView;
    const create = vi.fn<(request: CreateWorkspaceRequest) => Promise<WorkspaceStateView>>().mockResolvedValue(workspace);
    const navigate = vi.fn<(href: string) => void>();

    const restore = vi.fn<(prompt: string) => void>();
    const clear = vi.fn();

    await submitLaunch(
      state,
      "Create the composer\nwith defaults",
      [],
      create,
      navigate,
      restore,
      undefined,
      clear,
    );

    expect(create).toHaveBeenCalledWith({
      repo_root: "/repos/grove",
      project_cwd: "/repos/grove/webapp",
      // No title: the engine names an untitled workspace, never the prompt.
      // No agent and no branch plan: untouched pills contribute nothing, and
      // the engine resolves both from the same cascade the pills displayed.
      initial_prompt: "Create the composer\nwith defaults",
    });
    expect(navigate).toHaveBeenCalledWith("/w/workspace-1");
    expect(restore).not.toHaveBeenCalled();
    expect(clear).toHaveBeenCalledWith();
  });

  it("restores the prompt and preserves a rejected create without navigating", async () => {
    const refusal = new Error("branch already exists");
    const create = vi.fn<(request: CreateWorkspaceRequest) => Promise<WorkspaceStateView>>().mockRejectedValue(refusal);
    const navigate = vi.fn<(href: string) => void>();
    const restore = vi.fn<(prompt: string) => void>();
    const restoreDraft = vi.fn();
    const clear = vi.fn();
    const reject = vi.fn();

    await expect(
      submitLaunch(
        state,
        "Retry this task",
        [],
        create,
        navigate,
        restore,
        restoreDraft,
        clear,
        reject,
      ),
    ).rejects.toBe(refusal);

    expect(restore).toHaveBeenCalledWith("Retry this task");
    expect(restoreDraft).toHaveBeenCalledWith("Retry this task", []);
    expect(reject).toHaveBeenCalledOnce();
    expect(clear).not.toHaveBeenCalled();
    expect(navigate).not.toHaveBeenCalled();
  });

  it("carries staged files through to the request", async () => {
    const create = vi
      .fn<(request: CreateWorkspaceRequest) => Promise<WorkspaceStateView>>()
      .mockResolvedValue({ id: "workspace-2" } as WorkspaceStateView);

    await submitLaunch(
      state,
      "Read the spec",
      [{ name: "spec.md", size: 12, content_base64: "Zm9v" }],
      create,
      vi.fn<(href: string) => void>(),
      vi.fn<(prompt: string) => void>(),
    );

    expect(create.mock.calls[0]![0].attachments).toEqual([
      { name: "spec.md", content_base64: "Zm9v" },
    ]);
  });
});
