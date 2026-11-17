import { describe, expect, it } from "vitest";

import { branchPlanFor, buildCreateRequest } from "@/lib/grove/adapters/launch";
import type { LaunchState, LaunchValues } from "@/components/grove/launch/launch-state";

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
  branchName: "feature/launch",
  existingBranch: "feature/existing",
  remoteRef: "origin/feature/remote",
  localName: "feature/local",
  baseRef: "main",
  skipInit: true,
  titleOverride: null,
  ticket: { provider: "gitea", id: "42", title: "Launch" },
};

function state(touched: readonly (keyof LaunchValues)[] = []): LaunchState {
  return { values, touched: new Set(touched), seededRepo: values.repoRoot };
}

describe("buildCreateRequest title", () => {
  it("omits an untyped title so the engine names the workspace, never the prompt", () => {
    const request = buildCreateRequest(state(), "Build the landing page");
    expect("title" in request).toBe(false);
  });

  it("omits a whitespace-only override the same way", () => {
    const request = buildCreateRequest({ ...state(), values: { ...values, titleOverride: "   " } }, "Ship it");
    expect("title" in request).toBe(false);
  });

  it("sends a title the user typed, trimmed", () => {
    const request = buildCreateRequest({ ...state(), values: { ...values, titleOverride: "  parser fix " } }, "Ship it");
    expect(request.title).toBe("parser fix");
  });
});

describe("branchPlanFor", () => {
  it("covers every form branch mode", () => {
    expect(branchPlanFor({ ...values, branchMode: "auto" })).toEqual({ kind: "auto", base_ref: "main" });
    expect(branchPlanFor({ ...values, branchMode: "new" })).toEqual({ kind: "new_named", name: "feature/launch", base_ref: "main" });
    expect(branchPlanFor({ ...values, branchMode: "existing" })).toEqual({ kind: "existing_local", name: "feature/existing" });
    expect(branchPlanFor({ ...values, branchMode: "remote" })).toEqual({ kind: "track_remote", remote_ref: "origin/feature/remote", local_name: "feature/local" });
    expect(branchPlanFor({ ...values, branchMode: "root" })).toEqual({ kind: "root" });
  });
});

describe("buildCreateRequest", () => {
  it("sends an untouched nested project's own cwd", () => {
    expect(buildCreateRequest(state(), "Build the landing page\nwith the new composer")).toEqual({
      repo_root: "/repos/grove",
      project_cwd: "/repos/grove/webapp",
      // No agent and no branch plan: both are untouched, so the engine's
      // resolver answers them exactly as `GET /defaults` displayed them.
      initial_prompt: "Build the landing page\nwith the new composer",
      ticket: { provider: "gitea", id: "42", kind: "issue" },
    });
  });

  it("sends the explicitly selected working-directory path rather than its label", () => {
    // Labels are never retained in LaunchValues, so renaming one cannot change
    // the wire path recorded by an existing workspace.
    expect(
      buildCreateRequest(
        { values: { ...values, projectCwd: "packages/api" }, touched: new Set(["projectCwd"]), seededRepo: values.repoRoot },
        "Build it",
      ).project_cwd,
    ).toBe("packages/api");
  });

  it("omits project_cwd for an untouched top-level project", () => {
    const request = buildCreateRequest(
      {
        values: {
          ...values,
          projectCwd: null,
          selectedProjectCwd: "/repos/grove",
        },
        touched: new Set(), seededRepo: "/repos/grove",
      },
      "Use the configured working directory",
    );

    expect(request).not.toHaveProperty("project_cwd");
  });

  it("lets an explicit repository-root choice override a nested project", () => {
    expect(
      buildCreateRequest(
        { values: { ...values, projectCwd: null }, touched: new Set(["projectCwd"]), seededRepo: "/repos/grove" },
        "Start in the root",
      ).project_cwd,
    ).toBeNull();
  });

  it("omits an untouched placement, so the engine resolves the saved one", () => {
    // Since #1093 the engine reads the saved branch mode itself; sending what
    // the pill displays would pin today's default onto this create, and the
    // forced send it replaced was the window where a placeholder `auto` beat a
    // saved `root`.
    const seeded = state();
    const request = buildCreateRequest(
      { values: { ...seeded.values, branchMode: "root" }, touched: seeded.touched, seededRepo: "/repos/grove" },
      "Work in the root",
    );

    expect(request).not.toHaveProperty("branch_plan");
  });

  it("sends a chosen placement, and the chosen agent", () => {
    const chosen = state(["branchMode", "agentName"]);
    const request = buildCreateRequest(
      { ...chosen, values: { ...chosen.values, branchMode: "root" } },
      "Work in the root",
    );

    expect(request).toMatchObject({ branch_plan: { kind: "root" }, agent_name: "claude" });
  });

  it("rejects an invalid explicit custom model before it can enter a request", () => {
    expect(() =>
      buildCreateRequest(
        { values: { ...values, model: "invalid model", customModel: true }, touched: new Set(["model"]), seededRepo: "/repos/grove" },
        "Build it",
      ),
    ).toThrow("Use ASCII letters");
  });

  it("pins fields only after the user touches their controls", () => {
    expect(buildCreateRequest(state(["model", "runtime", "brief", "skipInit", "branchMode", "agentName"]), "Build it")).toEqual({
      repo_root: "/repos/grove",
      project_cwd: "/repos/grove/webapp",
      agent_name: "claude",
      initial_prompt: "Build it",
      model: "sonnet",
      runtime: "container",
      brief: true,
      skip_init: true,
      branch_plan: { kind: "auto", base_ref: "main" },
      ticket: { provider: "gitea", id: "42", kind: "issue" },
    });
  });
});

describe("the session mode rides the request only when touched", () => {
  it("omits `native` untouched, so the roster entry's default decides at create", () => {
    expect(buildCreateRequest(state([]), "Build it")).not.toHaveProperty("native");
  });

  it("pins a touched mode as `native`", () => {
    const touched = state(["native"]);
    const values = { ...touched.values, native: false };
    expect(buildCreateRequest({ ...touched, values }, "Build it")).toMatchObject({ native: false });
  });
});

describe("buildCreateRequest runtime and node", () => {
  it("names a remote block as runtime remote plus its block, and only when touched", () => {
    const remote = { ...values, runtime: "sandbox", node: "edge-1" };
    const touched = buildCreateRequest(
      { values: remote, touched: new Set(["runtime", "node"]), seededRepo: "/repos/grove" },
      "work",
    );
    expect(touched).toMatchObject({ runtime: "remote", remote_runtime: "sandbox", node: "edge-1" });
    const untouched = buildCreateRequest({ values: remote, touched: new Set(), seededRepo: "/repos/grove" }, "work");
    expect(untouched).not.toHaveProperty("runtime");
    expect(untouched).not.toHaveProperty("remote_runtime");
    expect(untouched).not.toHaveProperty("node");
  });

  it("keeps host and container as plain runtimes with no remote block", () => {
    const request = buildCreateRequest(state(["runtime"]), "work");
    expect(request.runtime).toBe("container");
    expect(request).not.toHaveProperty("remote_runtime");
  });
});
