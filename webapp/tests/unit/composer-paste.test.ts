import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const read = (path: string): string => readFileSync(new URL(`../../${path}`, import.meta.url), "utf8");

describe("clipboard files reuse each composer's existing attachment path", () => {
  it("lets assistant-ui own workspace paste rather than uploading twice", () => {
    const source = read("components/grove/workspace/thread.tsx");
    expect(source).toMatch(/<ComposerPrimitive\.Input\s+addAttachmentOnPaste/);
    expect(source).not.toContain("onPaste=");
  });
  it("stages landing clipboard files through the same addFiles as the picker", () => {
    // The editor became the vendored `Textarea`, so the handler moved element.
    // It did not move OWNER: a paste carrying files and a file picked from the
    // dialog must land in one `addFiles`, or the size and count refusals apply
    // to one path and not the other.
    const source = read("components/grove/launch/launch-surface.tsx");
    const handler = source.slice(source.indexOf("onPaste="), source.indexOf("onKeyDown="));
    expect(handler).toContain("event.clipboardData.files");
    expect(handler).toContain("files.length === 0) return");
    expect(handler).toContain("event.preventDefault()");
    expect(handler).toContain("launch.addFiles(files)");
    expect(source).toContain("onAdd={launch.addFiles}");
  });

  it("keeps landing paste on the editor itself, with no second drop handler beside it", () => {
    // One staging path, asserted as an absence. A `onDrop` added next to this
    // would double-stage the same files on browsers that fire both for a
    // dragged selection, and the second copy is silent — it passes the count
    // check until the user is one file from the limit.
    const source = read("components/grove/launch/launch-surface.tsx");
    expect(source.match(/onPaste=/g) ?? []).toHaveLength(1);
    expect(source).not.toContain("onDrop=");
  });
});
