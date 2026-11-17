import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  LAUNCH_INITIAL_STATE,
  launchReducer,
  type LaunchState,
} from "@/components/grove/launch/launch-state";
import {
  buildCreateRequest,
  currentPreview,
  launchVerdict,
  openProblems,
  previewKey,
} from "@/lib/grove/adapters/launch";
import { GroveClient, GroveProtocolError, type LaunchPreviewView } from "@/lib/grove/api";
import { submitLaunch } from "@/lib/grove/runtime/launch";
import { problemFor } from "@/lib/grove/adapters/launch-flow";

const REPO = "/r";

function view(problems: LaunchPreviewView["problems"] = []): LaunchPreviewView {
  return {
    agent_name: "claude",
    agent_kind: "claude_code",
    runtime: "host",
    remote_runtime: null,
    runtime_fallback_reason: null,
    native: true,
    placement: "root",
    branch_plan: { kind: "root" },
    agent_cwd: "",
    model: null,
    effort: null,
    brief: true,
    skip_init: false,
    sources: { runtime: "project" },
    problems,
    adjustments: [],
  };
}

const ready = (): LaunchState =>
  launchReducer(launchReducer(LAUNCH_INITIAL_STATE, { type: "set", values: { repoRoot: REPO } }), {
    type: "seed",
    values: {},
    repo: REPO,
  });

const previewed = (state: LaunchState, problems: LaunchPreviewView["problems"]): LaunchState =>
  launchReducer(state, {
    type: "preview",
    preview: { request: previewKey(state)!, view: view(problems) },
  });

const REMOTE_ROOT = {
  field: "branch_plan",
  error: "remote_requires_worktree",
  message: "A remote workspace needs an isolated branch.",
};

describe("a preview of THIS draft decides its problems", () => {
  it("refuses Send with a combination only the preview can see", () => {
    // Remote + current checkout is a choice the person BUILT; the untouched
    // defaults could never report it, so before S2 it was refused only at create.
    const built = launchReducer(ready(), { type: "set", values: { runtime: "lab", branchMode: "root" } });
    const state = previewed(built, [REMOTE_ROOT]);

    expect(openProblems(state)).toEqual([REMOTE_ROOT]);
    expect(() => buildCreateRequest(state, "go")).toThrow(REMOTE_ROOT.message);
  });

  it("is ignored once the draft has moved on, so it neither refuses nor clears", () => {
    const built = launchReducer(ready(), { type: "set", values: { runtime: "lab", branchMode: "root" } });
    const stale = launchReducer(previewed(built, [REMOTE_ROOT]), {
      type: "set",
      values: { branchMode: "auto" },
    });

    // A preview of the old draft must not refuse the new one…
    expect(currentPreview(stale)).toBeNull();
    expect(openProblems(stale)).toEqual([]);
    expect(() => buildCreateRequest(stale, "go")).not.toThrow();
  });

  it("does not let a stale clean preview clear the seed's problem", () => {
    const INCOMPLETE = { field: "branch_plan", error: "incomplete_default", message: "Name the branch." };
    const seeded = launchReducer(ready(), {
      type: "seed",
      values: { branchMode: "new" },
      repo: REPO,
      problems: [INCOMPLETE],
    });
    const cleanForOldDraft = launchReducer(seeded, {
      type: "preview",
      preview: { request: JSON.stringify({ repo_root: "/elsewhere" }), view: view([]) },
    });

    expect(openProblems(cleanForOldDraft)).toEqual([INCOMPLETE]);
  });

  it("treats an empty problem list as the engine accepting the request", () => {
    expect(openProblems(previewed(ready(), []))).toEqual([]);
  });

  it("returns the same state for the same answer, so the previewer cannot loop", () => {
    const state = ready();
    const preview = { request: previewKey(state)!, view: view() };
    const once = launchReducer(state, { type: "preview", preview });
    expect(launchReducer(once, { type: "preview", preview })).toBe(once);
  });

  it("previews only the launch half: the brief and files change no key", () => {
    // `previewKey` is built from `launchRequest`, so typing the brief cannot
    // trigger a preview — and the preview and the create share every launch
    // field by construction.
    const state = ready();
    const key = previewKey(state)!;
    expect(JSON.parse(key)).not.toHaveProperty("initial_prompt");
    const request = buildCreateRequest(state, "a long brief");
    const { initial_prompt: _prompt, ...launch } = request;
    expect(launch).toEqual(JSON.parse(key));
  });
});

describe("a create the daemon refuses lands on the same control", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("reads the refusal's field off a 422", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json(
          { detail: { error: "runtime_unavailable", message: "Docker is not running.", field: "runtime" } },
          { status: 422 },
        ),
      ),
    );
    const error = await GroveClient.create()
      .createWorkspace({ repo_root: REPO })
      .catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(GroveProtocolError);
    expect(error).toMatchObject({ code: "runtime_unavailable", field: "runtime", status: 422 });
  });

  it("keeps a field-less 500 off every pill, as the real route sends it", async () => {
    // Measured on POST /workspaces: a project that requires a container answers
    // a host create with 500 {error: grove_error, message} and NO field — it is
    // a plain GroveError, not an InvalidLaunch. It must stay a form-level error.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json(
          { detail: { error: "grove_error", message: "This project refuses to run its agents on the host." } },
          { status: 500 },
        ),
      ),
    );
    let state = ready();
    const create = (request: Parameters<GroveClient["createWorkspace"]>[0]) =>
      GroveClient.create().createWorkspace(request);
    const error = await submitLaunch(state, "go", [], create, vi.fn(), vi.fn(), undefined, undefined, undefined,
      (request, problem) => {
        state = launchReducer(state, { type: "refused", request, problem });
      },
    ).catch((caught: unknown) => caught);

    expect(error).toMatchObject({ code: "grove_error", field: null, status: 500 });
    expect(openProblems(state)).toEqual([]);
  });

  it("records it against the request it refused, and drops it once the draft changes", async () => {
    let state = launchReducer(ready(), { type: "set", values: { runtime: "container" } });
    const refusal = new GroveProtocolError("runtime_unavailable", "Docker is not running.", 422, "runtime");
    await expect(
      submitLaunch(
        state,
        "go",
        [],
        vi.fn().mockRejectedValue(refusal),
        vi.fn(),
        vi.fn(),
        undefined,
        undefined,
        undefined,
        (request, problem) => {
          state = launchReducer(state, { type: "refused", request, problem });
        },
      ),
    ).rejects.toBe(refusal);

    expect(openProblems(state)).toEqual([
      { field: "runtime", error: "runtime_unavailable", message: "Docker is not running." },
    ]);
    const changed = launchReducer(state, { type: "set", values: { runtime: "host" } });
    expect(openProblems(changed)).toEqual([]);
  });

  it("records nothing for a refusal that names no field", async () => {
    const record = vi.fn();
    await submitLaunch(
      ready(),
      "go",
      [],
      vi.fn().mockRejectedValue(new GroveProtocolError("grove_error", "boom", 500)),
      vi.fn(),
      vi.fn(),
      undefined,
      undefined,
      undefined,
      record,
    ).catch(() => undefined);

    expect(record).not.toHaveBeenCalled();
  });
});

describe("an open problem disarms Send and is readable at form level", () => {
  it("names a problem whose field no pill shows", () => {
    // `node` has no problem-bearing pill; `selector` has no control at all.
    for (const field of ["node", "selector"]) {
      const problem = { field, error: "x", message: `The ${field} is wrong.` };
      const state = previewed(ready(), [problem]);
      expect(launchVerdict(state), field).toBe(problem.message);
    }
  });

  it("names a problem whose pill is hidden right now", () => {
    // The branch pill does not render for the current checkout, so a branch
    // problem there would otherwise be invisible.
    const current = launchReducer(ready(), { type: "set", values: { branchMode: "root" } });
    expect(launchVerdict(previewed(current, [REMOTE_ROOT]))).toBe(REMOTE_ROOT.message);
  });

  it("says nothing when the engine has nothing to say, and nothing for a missing project", () => {
    expect(launchVerdict(previewed(ready(), []))).toBeNull();
    expect(launchVerdict(LAUNCH_INITIAL_STATE)).toBeNull();
  });
});

describe("the composer's Send obeys the verdict", () => {
  // `useLaunchSubmit` needs a router and a live draft to render, so the wiring
  // is pinned at the source: the verdict must gate `canSubmit` AND feed the
  // form-level `refusal` line both surfaces already render.
  const source = readFileSync("lib/grove/runtime/launch.ts", "utf8");

  it("disarms Send while the verdict stands", () => {
    expect(source).toContain("const blocking = launchVerdict(controls);");
    expect(source).toMatch(/canSubmit:[^\n]*blocking === null/);
  });

  it("shows the verdict on the form-level refusal line", () => {
    expect(source).toContain("refusal: refusal ?? blocking,");
  });
});

describe("the refusals #1093 S4 turned into 422s with a field", () => {
  // The exact (error, field) pairs the daemon now sends for refusals that used
  // to escape as a field-less 500. Each must reach a pill AND the form line.
  it.each([
    ["container_required", "runtime", "runtime"],
    ["project_cwd_outside_repo", "project_cwd", "projectCwd"],
  ] as const)("%s on %s lands on its control and disarms Send", async (code, field, draftField) => {
    let state = ready();
    const refusal = new GroveProtocolError(code, `refused: ${code}`, 422, field);
    await submitLaunch(state, "go", [], vi.fn().mockRejectedValue(refusal), vi.fn(), vi.fn(),
      undefined, undefined, undefined,
      (request, problem) => {
        state = launchReducer(state, { type: "refused", request, problem });
      },
    ).catch(() => undefined);

    expect(problemFor(openProblems(state), draftField)).toBe(`refused: ${code}`);
    expect(launchVerdict(state)).toBe(`refused: ${code}`);
  });
});
