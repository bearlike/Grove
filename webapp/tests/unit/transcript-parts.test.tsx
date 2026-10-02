import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { FileEditPart } from "@/components/grove/workspace/file-edit-part";
import { fileIconSlug } from "@/components/grove/workspace/file-type-icon";

const EDIT = {
  path: "/home/agent/.worktrees/x/src/grove/core/manager.py",
  displayPath: "src/grove/core/manager.py",
  oldText: "one\ntwo\nthree\n",
  newText: "one\ntwo prime\nthree\nfour\n",
  // A provider that reports no per-call detail. The invocation line is absent
  // here on purpose; `tool-call-part.test.tsx` covers the case where it is not.
  tool: null,
};

describe("FileEditPart", () => {
  const markup = renderToStaticMarkup(<FileEditPart data={EDIT} />);

  it("starts collapsed — a transcript of a hundred edits must not paint a hundred diffs", () => {
    expect(markup).toContain('data-collapsed="true"');
  });

  it("renders no diff body while collapsed", () => {
    expect(markup).not.toContain("diff-viewer");
    expect(markup).not.toContain("two prime");
  });

  it("still shows the signal: the path, and what moved", () => {
    expect(markup).toContain("manager.py");
    expect(markup).toContain("+2");
    expect(markup).toContain("−1");
  });

  it("keeps the full path reachable for a path the header truncates", () => {
    expect(markup).toContain(EDIT.path);
  });

  it("draws a coloured type icon, not the vendored monochrome text chip", () => {
    expect(markup).toContain('data-testid="file-type-icon"');
    expect(markup).not.toContain("diff-viewer-file-badge");
  });
});

describe("file type icons", () => {
  it("names the VS Code icon for an extension, an exact filename and a dotfile", () => {
    expect(fileIconSlug("src/a/b/manager.py")).toBe("vscode-icons:file-type-python");
    expect(fileIconSlug("web/app.tsx")).toBe("vscode-icons:file-type-reactts");
    expect(fileIconSlug("web/package.json")).toBe("vscode-icons:file-type-npm");
    expect(fileIconSlug(".gitignore")).toBe("vscode-icons:file-type-git");
  });

  it("falls back to the set's generic page rather than a lookalike of another language", () => {
    expect(fileIconSlug("notes.weirdext")).toBe("vscode-icons:default-file");
  });
});

describe("the transcript port carries no message action bars", () => {
  // A SOURCE assertion, deliberately. `thread.tsx` is a declared port whose
  // maintenance instruction is "re-copy the vendored file and re-apply the
  // deltas" — and a re-copy silently restores copy, reload, export, edit and
  // both branch pickers, none of which Grove can honour. There is no rendered
  // artifact to assert on, because the correct render is their absence.
  // Comments are blanked first, for the reason `scripts/lint-styling.ts` blanks
  // them: the port's own header EXPLAINS which controls were removed, so a raw
  // scan flags the documentation of the fix as the fix's absence.
  const source = readFileSync("components/grove/workspace/thread.tsx", "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:])\/\/[^\n]*/g, "$1");

  it("imports no action-bar or branch-picker primitive", () => {
    for (const primitive of [
      "ActionBarPrimitive",
      "ActionBarMorePrimitive",
      "BranchPickerPrimitive",
    ]) {
      expect(source).not.toContain(primitive);
    }
  });

  it("reserves no vertical space for the footer it no longer renders", () => {
    expect(source).not.toContain("aui_assistant-message-footer");
    expect(source).not.toContain("-mb-7.5");
  });

  it("keeps the scroll-to-bottom arrow, which is a different concern", () => {
    expect(source).toContain("ThreadPrimitive.ScrollToBottom");
    expect(source).toContain("Scroll to bottom");
  });

  it("states a row's real height rather than estimating it", () => {
    // Upstream's `content-visibility: auto` + `contain-intrinsic-size: auto
    // 200px` is the one delta a re-copy would silently undo AND the one whose
    // damage is invisible in a test that renders: the rows are correct, it is
    // the SCROLL POSITION that moves. Measured on a 662-row transcript, 18,000px
    // of upward wheel input travelled 51,199–52,364px because ~48 height
    // corrections landed above the viewport; without it, 18,000px exactly.
    // Both roots, hence both halves of the assertion.
    expect(source).not.toContain("content-visibility");
    expect(source).not.toContain("contain-intrinsic-size");
  });
});
