import { REMOTE_BRANCH_REASON } from "@/components/grove/launch/launch-summary";
import { runtimeHeadline } from "@/components/grove/launch/controls/runtime-pill";
import { readFileSync } from "node:fs";

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { LaunchNote, noteFor, type NoteInput } from "@/components/grove/launch/launch-summary";
import {
  LAUNCH_INITIAL_STATE,
  launchReducer,
  type LaunchState,
} from "@/components/grove/launch/launch-state";
import { profileLabels } from "@/components/grove/launch/controls/profile-pill";
import { launchRequest } from "@/lib/grove/adapters/launch";

const REPO = "/repos/grove";

function renderNote(tone: "note" | "warning" | "caution", undo = false): string {
  return renderToStaticMarkup(
    <LaunchNote
      tone={tone}
      message={tone === "warning" ? "Engine selected a profile" : "Runs on this host · current checkout"}
      runtime="host"
      testId="launch-summary"
      undo={undo ? () => undefined : undefined}
      undoField={undo ? "agent_name" : undefined}
    />,
  );
}

function draft(values: LaunchState["values"], touched: readonly (keyof LaunchState["values"])[]): LaunchState {
  return { ...LAUNCH_INITIAL_STATE, values, touched: new Set(touched), seededRepo: REPO };
}

describe("the launch outcome note", () => {
  it("renders the neutral, adjustment, and refusal tones as one status line", () => {
    const grey = renderNote("note");
    const amber = renderNote("warning", true);
    const red = renderNote("caution");

    expect(grey).toContain('data-note="note"');
    expect(grey).toContain('aria-live="off"');
    expect(amber).toContain('data-note="warning"');
    expect(amber).toContain('aria-live="polite"');
    expect(amber).toContain('data-undo-field="agent_name"');
    expect(red).toContain('data-note="caution"');
    expect(red).toContain('role="status"');
  });
});

describe("what the note says, from the cases seen on the deployed shelf", () => {
  const base: NoteInput = {
    line: "Runs on this host · current checkout",
    error: null,
    refusal: null,
    adjustments: [],
    placementSwitched: false,
    profileUndo: null,
  };
  const profile = { field: "agent_name", reason: "Uses Claude Code (sandbox) on sandbox." };

  it("names both changes when sandbox moves the profile and the draft moved the checkout", () => {
    const note = noteFor({ ...base, adjustments: [profile], placementSwitched: true, profileUndo: "Claude Code (via Gateway)" });
    expect(note.tone).toBe("warning");
    expect(note.message).toBe(`Uses Claude Code (sandbox) on sandbox. ${REMOTE_BRANCH_REASON}`);
    expect(note.undoProfile).toBe("Claude Code (via Gateway)");
  });

  it("offers no Undo for the branch switch alone, since its checkout would be refused", () => {
    const note = noteFor({ ...base, placementSwitched: true });
    expect(note.message).toBe(REMOTE_BRANCH_REASON);
    expect(note.undoProfile).toBeNull();
  });


  it("lets a refusal outrank everything", () => {
    expect(noteFor({ ...base, adjustments: [profile], refusal: "Choose a branch to continue" }).tone).toBe("caution");
  });
});

describe("remote placement and profile intent", () => {
  it("changes a current checkout to auto and remembers the Undo target", () => {
    const state = draft(
      {
        ...LAUNCH_INITIAL_STATE.values,
        repoRoot: REPO,
        agentName: "Claude Code (via Gateway)",
        runtime: "host",
        branchMode: "root",
      },
      ["branchMode"],
    );

    const changed = launchReducer(state, { type: "set", values: { runtime: "sandbox" } });

    expect(changed.values.branchMode).toBe("auto");
    expect(changed.touched.has("runtime")).toBe(true);
    expect(changed.touched.has("branchMode")).toBe(true);
    expect(changed.runtimePlacementUndo).toBe("root");
    expect(changed.values.agentName).toBeNull();
    expect(changed.profileUndo).toBe("Claude Code (via Gateway)");

    // Leaving the remote runtime restores the checkout the runtime displaced.
    const back = launchReducer(changed, { type: "set", values: { runtime: "host" } });
    expect(back.values.branchMode).toBe("root");
    expect(back.runtimePlacementUndo).toBeNull();
  });

  it("sends an Agent pill choice as agent_kind until a Profile pick makes it explicit", () => {
    const tool = draft(
      { ...LAUNCH_INITIAL_STATE.values, repoRoot: REPO, agentKind: "claude_code" },
      ["agentKind"],
    );
    const profile = draft(
      { ...LAUNCH_INITIAL_STATE.values, repoRoot: REPO, agentName: "Claude Code (via Gateway)" },
      ["agentName"],
    );

    expect(launchRequest(tool)).toMatchObject({ agent_kind: "claude_code" });
    expect(launchRequest(tool)).not.toHaveProperty("agent_name");
    expect(launchRequest(profile)).toMatchObject({ agent_name: "Claude Code (via Gateway)" });
    expect(launchRequest(profile)).not.toHaveProperty("agent_kind");
  });

  it("drops the words every profile of a tool repeats, whatever the tool is called", () => {
    // The live roster's shape: the Agent pill says "Claude", the names say
    // "Claude Code", so a cut keyed on the tool's display word matched nothing.
    expect(
      profileLabels([
        "Claude Code (default)",
        "Claude Code (via Example Gateway)",
        "Claude Code (via Example Gateway, Dev Containers)",
        "Claude Code (sandbox)",
      ]),
    ).toEqual(["default", "via Example Gateway", "via Example Gateway, Dev Containers", "sandbox"]);
  });

  it("keeps a name whole when nothing is shared or nothing would remain", () => {
    expect(profileLabels(["Codex"])).toEqual(["Codex"]);
    expect(profileLabels(["Gateway Claude", "Claude Code"])).toEqual(["Gateway Claude", "Claude Code"]);
    // "Claude" is the only word both can lose while each keeps a remainder.
    expect(profileLabels(["Claude Code", "Claude Code (x)"])).toEqual(["Code", "Code (x)"]);
  });
});

describe("launch errors have one visual home", () => {
  it("leaves no destructive bare paragraph in either launch surface", () => {
    for (const file of [
      "components/grove/launch/launch-surface.tsx",
      "components/grove/launch/more-options-dialog.tsx",
    ]) {
      const source = readFileSync(file, "utf8");
      expect(source).not.toMatch(/<p[^>]*text-destructive/);
      expect(source).not.toMatch(/<p[^>]*data-testid="(?:launch|create)-(?:error|refusal)"/);
    }
    const dialog = readFileSync("components/grove/launch/more-options-dialog.tsx", "utf8");
    expect(dialog).toContain('noteTestIds={{ error: "create-error", refusal: "create-refusal" }}');
  });
});

describe("a disabled runtime row says what is wrong in a few words", () => {
  it("keeps the reason and leaves the remedy to the hover detail", () => {
    expect(
      runtimeHeadline(
        "container runtime unavailable: @devcontainers/cli: 'devcontainer' not found on PATH — install the devcontainer CLI",
      ),
    ).toBe("Container runtime unavailable");
    expect(runtimeHeadline("docker: not found on PATH")).toBe("Docker: not found on PATH");
    expect(runtimeHeadline("Docker is not running.")).toBe("Docker is not running");
  });
});

describe("the draft's own placement switch reads like the engine's", () => {
  it("uses the engine's sentence word for word", () => {
    const engine = readFileSync("../src/grove/core/launch_resolution.py", "utf8");
    expect(engine).toContain(`reason="${REMOTE_BRANCH_REASON}"`);
  });
});
