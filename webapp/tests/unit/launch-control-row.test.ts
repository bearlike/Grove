import { readdirSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

/**
 * TWO PILLS SHARING A `kind` IS A SILENT DEAD CONTROL, so the census is a test.
 *
 * `LaunchPillKind` is the row's open-menu key (`open={openKind === kind}`), so
 * a duplicate makes one click open two popovers stacked on each other and
 * leaves whichever renders second unreachable underneath the first. Nothing
 * throws, nothing fails to compile, and the pill looks perfectly normal — the
 * user simply cannot select anything in it. `project` and `directory` shipped
 * that way, which is why this is pinned at the level the defect lives at:
 * across FILES, where no single component can see the collision.
 *
 * SOURCE assertions in the style of `app-shell.test.ts`: these are JSX props on
 * components that need a query client, a fleet stream and a popover to render,
 * so there is no artifact a `node`-environment suite can inspect instead.
 */
const CONTROLS_DIR = "components/grove/launch/controls";

const pillFiles = readdirSync(CONTROLS_DIR)
  .filter((name) => name.endsWith("-pill.tsx"))
  .sort();

function kindOf(file: string): string {
  const source = readFileSync(`${CONTROLS_DIR}/${file}`, "utf8");
  const match = /\bkind="([a-z]+)"/.exec(source);
  expect(match, `${file} passes no kind= to LaunchPill`).not.toBeNull();
  return match![1]!;
}

describe("the launch pill census", () => {
  it("found every pill file, so a clean result is not an empty scan", () => {
    // The absence-check rule: a census that reports "no duplicates" after
    // reading zero files is indistinguishable from one that read them all.
    expect(pillFiles.length).toBeGreaterThanOrEqual(6);
    expect(pillFiles).toContain("project-pill.tsx");
    expect(pillFiles).toContain("working-directory-pill.tsx");
  });

  it("gives every pill its OWN kind — a shared one is an unreachable menu", () => {
    const kinds = pillFiles.map(kindOf);

    expect(new Set(kinds).size).toBe(kinds.length);
  });

  it("keeps the project and directory pills on separate keys, by name", () => {
    // Named explicitly as well as counted: these two are the pair that
    // collided, and a future rename that reintroduces it should fail on the
    // sentence describing the bug rather than only on an arity check.
    expect(kindOf("project-pill.tsx")).toBe("project");
    expect(kindOf("working-directory-pill.tsx")).toBe("directory");
  });
});

describe("what a pill displays", () => {
  it("spells out every value — no pill collapses to a bare glyph", () => {
    // Three controls (agent, runtime, branch) used to render as an unlabelled
    // icon until the user touched them, on the argument that the mark said
    // enough. A mark cannot tell two agents apart, and the row wraps now, so
    // the width that bought is no longer needed.
    for (const file of pillFiles) {
      expect(readFileSync(`${CONTROLS_DIR}/${file}`, "utf8")).not.toContain("collapsed=");
    }
  });

  it("reads the PROJECT's own cwd, never the working directory beside it", () => {
    // `projectCwd` is the directory the user picked and belongs to the other
    // pill; reading it here showed no selection after a project change and
    // matched no project after a directory change, so the project could not be
    // switched a second time.
    const project = readFileSync(`${CONTROLS_DIR}/project-pill.tsx`, "utf8");

    expect(project).toContain("value={values.selectedProjectCwd}");
    expect(project).not.toContain("value={values.projectCwd}");
  });
});

describe("the row is two authored rows, not one that wraps where it runs out", () => {
  const row = readFileSync(`${CONTROLS_DIR}/control-row.tsx`, "utf8");

  it("groups WHERE the work happens above WHO does it", () => {
    // Pinned as an order because the split is the point: a width-driven wrap
    // put whichever pill happened to overflow on line two, which orphaned one
    // control on a project with no configured directories.
    const at = (pill: string) => row.indexOf(`<${pill} />`);

    expect(at("ProjectPill")).toBeLessThan(at("WorkingDirectoryPill"));
    expect(at("WorkingDirectoryPill")).toBeLessThan(at("BranchPill"));
    expect(at("BranchPill")).toBeLessThan(at("AgentPill"));
    expect(at("AgentPill")).toBeLessThan(at("ModelPill"));
    expect(at("ModelPill")).toBeLessThan(at("RuntimePill"));
  });

  it("stacks the two groups, and lets each wrap internally on a narrow window", () => {
    expect(row).toContain("flex min-w-0 flex-1 flex-col gap-1");
    expect(row.match(/flex-wrap/g) ?? []).toHaveLength(2);
  });

  it("anchors Send to the last line, so the composer's corner does not move", () => {
    const surface = readFileSync("components/grove/launch/launch-surface.tsx", "utf8");

    expect(surface).toContain('<ComposerToolbar className="items-end gap-2">');
  });
});
