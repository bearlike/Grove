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

// The row's own layout — which pills sit on which line, and where the toolbar
// anchors Send — moved to `launch-layout.test.tsx` with the composer redesign.
// It is not asserted here any more: this file is the cross-FILE census of what
// the pills THEMSELVES declare, which is a property no layout can change.

/**
 * A LABEL SHARED ACROSS TWO PILLS IS THE SAME BUG ONE LAYER UP.
 *
 * The `kind` collision above makes a control dead; a label collision makes it
 * *ambiguous*, which is worse in one respect — the control works, so nobody
 * files it as broken, they just pick the wrong one. It shipped exactly that
 * way: the branch pill offered "Repo root" (Grove cuts no worktree) while the
 * working-directory pill offered "Repository root" (the agent starts at the top
 * of the tree). Two different questions, near-identical words, adjacent in one
 * row.
 *
 * Pinned as a cross-FILE census for the same reason as the kind check: each
 * file is individually correct and only their relationship is wrong.
 */
/**
 * Two labels are "the same" if a user would read them as the same thing.
 *
 * EXACT equality would not have caught the defect this exists for: the pair was
 * "Repo root" and "Repository root", which are different strings and identical
 * meanings. So the comparison normalizes the abbreviations that actually
 * collided rather than pretending to a general theory of synonyms — a list to
 * extend the next time a real pair slips through, not a heuristic to trust.
 */
function normalizeLabel(label: string): string {
  return label
    .toLowerCase()
    .replace(/[^a-z ]/g, "")
    .split(/\s+/)
    .map((word) => ({ repo: "repository", dir: "directory", wd: "directory" })[word] ?? word)
    .filter(Boolean)
    .sort()
    .join(" ");
}

function optionLabelsOf(file: string): string[] {
  const source = readFileSync(`${CONTROLS_DIR}/${file}`, "utf8");
  // `label: "…"` covers the definition tables the pills build options from,
  // which is where every user-visible option string in this row is authored.
  return [...source.matchAll(/\blabel:\s*"([^"]+)"/g)].map((match) => match[1]!);
}

describe("launch control row option labels", () => {
  it("examines every pill in the row", () => {
    // An absence check that scanned nothing reports clean exactly like one that
    // scanned everything, so state what was covered.
    expect(pillFiles.length).toBeGreaterThanOrEqual(5);
  });

  it("never offers the same label from two different pills", () => {
    const owners = new Map<string, string[]>();
    for (const file of pillFiles) {
      for (const label of optionLabelsOf(file)) {
        const key = normalizeLabel(label);
        owners.set(key, [...(owners.get(key) ?? []), file]);
      }
    }
    const shared = [...owners.entries()].filter(([, files]) => new Set(files).size > 1);
    expect(shared, `these labels are offered by more than one pill: ${JSON.stringify(shared)}`)
      .toEqual([]);
  });

  it("keeps the branch pill's glyphs out of the folder family the neighbouring controls own", () => {
    // The project control is a repository and the working-directory control is
    // a path, so both legitimately wear folder marks. A folder in the BRANCH
    // pill therefore reads as one of them — `FolderRootIcon` did, sitting next
    // to the project mark's `FolderGit2Icon`.
    const source = readFileSync(`${CONTROLS_DIR}/branch-pill.tsx`, "utf8");
    // Lookbehind excludes `readonly glyph: LucideIcon` — the TYPE declaration,
    // not an entry. Without it the census asserts against the annotation and
    // fails for a reason that has nothing to do with any glyph.
    const glyphs = [...source.matchAll(/(?<!readonly )\bglyph:\s*(\w+)/g)].map(
      (match) => match[1]!,
    );
    expect(glyphs.length).toBeGreaterThanOrEqual(5);
    for (const glyph of glyphs) expect(glyph).toMatch(/^Git/);
  });
});
