import { describe, expect, it } from "vitest";

import { branchPlanFor, buildCreateRequest, deriveTitle } from "@/lib/grove/adapters/launch";
import type { LaunchState, LaunchValues } from "@/components/grove/launch/launch-state";

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
  return { values, touched: new Set(touched) };
}

describe("deriveTitle", () => {
  it("uses an untitled fallback when the brief is blank", () => {
    expect(deriveTitle("   \n  ")).toBe("Untitled task");
    expect(deriveTitle("")).toBe("Untitled task");
  });

  it("is a short hex digest, never a slice of the brief", () => {
    const title = deriveTitle("Build the landing page");
    expect(title).toMatch(/^[0-9a-f]{10}$/);
    expect(title).not.toContain("Build");
  });

  it("is stable for the same brief and different for a different one", () => {
    expect(deriveTitle("Build the landing page")).toBe(deriveTitle("Build the landing page"));
    expect(deriveTitle("Build the landing page")).not.toBe(deriveTitle("Build the fleet page"));
  });

  it("digests the WHOLE brief, not just its first line", () => {
    // A first-line rule made these two collide, which is exactly the failure a
    // digest removes: two workspaces named the same thing for different work.
    expect(deriveTitle("Fix the bug\nin the parser")).not.toBe(
      deriveTitle("Fix the bug\nin the renderer"),
    );
  });

  it("stays inside the wire limit however long the brief is", () => {
    expect(deriveTitle("x".repeat(50_000)).length).toBeLessThanOrEqual(120);
  });

  it("ignores surrounding whitespace so a stray newline is not a new name", () => {
    expect(deriveTitle("  Ship it  ")).toBe(deriveTitle("Ship it"));
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
      agent_name: "claude",
      // The default name is a digest of the whole brief, so it is asserted
      // by shape: pinning the literal would just re-encode md5 in a test.
      title: expect.stringMatching(/^[0-9a-f]{10}$/),
      initial_prompt: "Build the landing page\nwith the new composer",
      // Always present, even untouched — see the next test for why this one
      // field cannot follow the rule the rest of them do.
      branch_plan: { kind: "auto", base_ref: "main" },
      ticket: { provider: "gitea", id: "42", kind: "issue" },
    });
  });

  it("sends the explicitly selected working-directory path rather than its label", () => {
    // Labels are never retained in LaunchValues, so renaming one cannot change
    // the wire path recorded by an existing workspace.
    expect(
      buildCreateRequest(
        { values: { ...values, projectCwd: "packages/api" }, touched: new Set(["projectCwd"]) },
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
        touched: new Set(),
      },
      "Use the configured working directory",
    );

    expect(request).not.toHaveProperty("project_cwd");
  });

  it("lets an explicit repository-root choice override a nested project", () => {
    expect(
      buildCreateRequest(
        { values: { ...values, projectCwd: null }, touched: new Set(["projectCwd"]) },
        "Start in the root",
      ).project_cwd,
    ).toBeNull();
  });

  it("sends the branch plan the pill DISPLAYS, even when the user never touched it", () => {
    // The regression: branch_plan is the one field whose omission is not
    // "resolve it from the cascade later". CreateWorkspaceRequest defaults it
    // to AutoBranch() and nothing engine-side reads defaults.branch_mode, so a
    // user whose saved default is `root` would see the pill say "Repo root"
    // and get an ordinary worktree — the display and the behaviour disagreeing
    // silently, on the one control that decides whether isolation exists.
    const seeded = state();
    const request = buildCreateRequest(
      { values: { ...seeded.values, branchMode: "root" }, touched: seeded.touched },
      "Work in the root",
    );

    expect(request.branch_plan).toEqual({ kind: "root" });
  });

  it("rejects an invalid explicit custom model before it can enter a request", () => {
    expect(() =>
      buildCreateRequest(
        { values: { ...values, model: "invalid model", customModel: true }, touched: new Set(["model"]) },
        "Build it",
      ),
    ).toThrow("Use ASCII letters");
  });

  it("pins fields only after the user touches their controls", () => {
    expect(buildCreateRequest(state(["model", "runtime", "brief", "skipInit", "branchMode"]), "Build it")).toEqual({
      repo_root: "/repos/grove",
      project_cwd: "/repos/grove/webapp",
      agent_name: "claude",
      title: expect.stringMatching(/^[0-9a-f]{10}$/),
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
