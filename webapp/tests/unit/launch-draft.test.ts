import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import {
  LAUNCH_INITIAL_STATE,
  launchReducer,
  type LaunchState,
  type LaunchValues,
} from "@/components/grove/launch/launch-state";
import { buildCreateRequest, launchBlocker } from "@/lib/grove/adapters/launch";

const REPO = "/repos/grove";
const OTHER = "/repos/other";

/** The cascade's answer for a project whose saved default is to work in place. */
const SAVED_ROOT: Partial<LaunchValues> = {
  agentName: "claude",
  runtime: "host",
  brief: true,
  branchMode: "root",
  baseRef: null,
  skipInit: true,
};

const pick = (state: LaunchState, values: Partial<LaunchValues>): LaunchState =>
  launchReducer(state, { type: "set", values });
const seed = (state: LaunchState, values: Partial<LaunchValues>, repo: string): LaunchState =>
  launchReducer(state, { type: "seed", values, repo });

describe("a send before the project's defaults answer", () => {
  it("builds the same request as one sent after, because nothing untouched is sent", () => {
    // The incident slice 1 guarded: before `/defaults` returned, `branchMode`
    // was the placeholder `auto`, the builder had to send a plan, and a send in
    // that window cut a worktree for a project whose saved answer was `root`.
    // Since #1093 the plan is omitted until touched, so the window is gone:
    // early and late sends are byte-identical and the engine resolves both.
    const early = pick(LAUNCH_INITIAL_STATE, { repoRoot: REPO });
    const late = seed(early, SAVED_ROOT, REPO);

    expect(launchBlocker(early)).toBeNull();
    expect(buildCreateRequest(early, "Fix it")).toEqual(buildCreateRequest(late, "Fix it"));
    expect(buildCreateRequest(early, "Fix it")).not.toHaveProperty("branch_plan");
    expect(buildCreateRequest(early, "Fix it")).not.toHaveProperty("agent_name");
  });

  it("is unchanged by a project switch, for the same reason", () => {
    const first = seed(pick(LAUNCH_INITIAL_STATE, { repoRoot: REPO }), SAVED_ROOT, REPO);
    const switched = pick(first, { repoRoot: OTHER });

    expect(launchBlocker(switched)).toBeNull();
    expect(buildCreateRequest(switched, "Fix it")).not.toHaveProperty("branch_plan");
  });

  it("does not wait when the person already chose the branch", () => {
    const chosen = pick(LAUNCH_INITIAL_STATE, {
      repoRoot: REPO,
      agentName: "claude",
      branchMode: "new",
      branchName: "feat/x",
    });

    expect(buildCreateRequest(chosen, "Fix it").branch_plan).toMatchObject({
      kind: "new_named",
      name: "feat/x",
    });
  });


  it("names the missing project before anything else", () => {
    expect(launchBlocker(LAUNCH_INITIAL_STATE)).toBe("Choose a project first");
  });
});

describe("the seed records which project it answered for", () => {
  it("records the repo even when every value already matched", () => {
    // A defaults response identical to the initial values still ANSWERED, and
    // Send must learn that; otherwise a project whose saved answers equal the
    // placeholders could never be sent.
    const chosen = pick(LAUNCH_INITIAL_STATE, { repoRoot: REPO });
    const seeded = seed(chosen, { branchMode: "auto" }, REPO);

    expect(seeded.seededRepo).toBe(REPO);
    expect(seeded).not.toBe(chosen);
  });

  it("returns the SAME state when nothing moved, so the seed effect cannot loop", () => {
    const seeded = seed(pick(LAUNCH_INITIAL_STATE, { repoRoot: REPO }), SAVED_ROOT, REPO);

    expect(seed(seeded, SAVED_ROOT, REPO)).toBe(seeded);
  });

  it("never overwrites a choice the person made while the response was in flight", () => {
    const chosen = pick(pick(LAUNCH_INITIAL_STATE, { repoRoot: REPO }), { runtime: "container" });
    const seeded = seed(chosen, SAVED_ROOT, REPO);

    expect(seeded.values.runtime).toBe("container");
    expect(buildCreateRequest(seeded, "Fix it")).toMatchObject({ runtime: "container" });
  });
});

describe("More options edits the one draft", () => {
  it("leaves an untouched brief, model and runtime to the engine", () => {
    // The deleted dialog sent its own literal per field; the one builder
    // omits whatever the person never touched, on every surface.
    const ready = seed(pick(LAUNCH_INITIAL_STATE, { repoRoot: REPO }), SAVED_ROOT, REPO);
    const request = buildCreateRequest(ready, "Fix it");

    for (const field of ["brief", "model", "runtime", "native", "skip_init", "title"]) {
      expect(request, field).not.toHaveProperty(field);
    }
  });

  it("carries a title, brief choice and ticket set in the dialog", () => {
    const ready = seed(pick(LAUNCH_INITIAL_STATE, { repoRoot: REPO }), SAVED_ROOT, REPO);
    const filled = pick(ready, {
      titleOverride: "Fix the login race",
      brief: false,
      ticket: { provider: "gitea", id: "42" },
    });

    expect(buildCreateRequest(filled, "Fix it")).toMatchObject({
      title: "Fix the login race",
      brief: false,
      ticket: { provider: "gitea", id: "42", kind: "issue" },
    });
  });

  it("is mounted once, in the shell, around both creation surfaces", () => {
    const shell = readFileSync("components/grove/shell/app-shell.tsx", "utf8");
    const overlays = readFileSync("components/grove/fleet/fleet-overlays.tsx", "utf8");
    const surface = readFileSync("components/grove/launch/launch-surface.tsx", "utf8");

    expect(shell).toContain("<LaunchDraftProvider>");
    expect(overlays).toContain("<MoreOptionsDialog />");
    // Neither surface may start a second draft of its own.
    for (const source of [surface, overlays]) {
      expect(source).not.toContain("LaunchStateProvider");
      expect(source).not.toContain("useLaunchSubmit(");
    }
  });
});
