import { describe, expect, it } from "vitest";

import {
  projectSelectionValues,
  resolveLaunchProject,
} from "@/components/grove/launch/controls/project-pill";

/**
 * The split under test: `projectCwd` is what the USER picked, cleared on every
 * project switch; `selectedProjectCwd` is the project's own identity, kept.
 *
 * That separation is what lets `buildCreateRequest` omit `project_cwd` for an
 * untouched pill — and omission is the only way the engine's own
 * `agent_cwds.default` can ever be reached from this surface, because it is
 * consulted exactly when nothing was specified. The serialization rule itself
 * is pinned in the launch-adapters suite; this file pins the state it reads.
 */
describe("projectSelectionValues", () => {
  it("remembers a nested project's directory as identity, not as a picked value", () => {
    expect(projectSelectionValues({ repoRoot: "/repos/grove", cwd: "/repos/grove/webapp" })).toEqual({
      repoRoot: "/repos/grove",
      projectCwd: null,
      selectedProjectCwd: "/repos/grove/webapp",
    });
  });

  it("leaves a top-level project unspecified, so the cascade's default applies", () => {
    // A top-level project's cwd IS its repo root and is non-null, so seeding it
    // into `projectCwd` would send a value for every create and make
    // `agent_cwds.default` permanently unreachable — invisibly, since the
    // resulting workspace still starts at the root and looks correct.
    expect(projectSelectionValues({ repoRoot: "/repos/grove", cwd: "/repos/grove" })).toEqual({
      repoRoot: "/repos/grove",
      projectCwd: null,
      selectedProjectCwd: "/repos/grove",
    });
  });

  it("clears a directory chosen under a different project", () => {
    const switched = projectSelectionValues({ repoRoot: "/repos/other", cwd: "/repos/other" });
    expect(switched.projectCwd).toBeNull();
    expect(switched.repoRoot).toBe("/repos/other");
  });
});

describe("resolveLaunchProject", () => {
  const choices = [
    { repoRoot: "/repos/grove", cwd: "/repos/grove", label: "Grove" },
    { repoRoot: "/repos/other", cwd: "/repos/other", label: "Other" },
  ] as const;

  it("restores the remembered project when the launch query names an unavailable project", () => {
    expect(
      resolveLaunchProject({
        choices,
        requestedCwd: "/repos/removed",
        remembered: { repoRoot: "/repos/other", cwd: "/repos/other", label: "" },
      }),
    ).toEqual(choices[1]);
  });

  it("waits for the project catalog before deciding a query is stale", () => {
    expect(
      resolveLaunchProject({
        choices: [],
        requestedCwd: "/repos/removed",
        remembered: { repoRoot: "/repos/other", cwd: "/repos/other", label: "" },
      }),
    ).toBeNull();
  });
});
