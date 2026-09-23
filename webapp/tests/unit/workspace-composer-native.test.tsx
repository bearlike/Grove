import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

/**
 * The workspace reply composer, on assistant-ui's own `elements/composer`
 * anatomy driven by `ComposerPrimitive`.
 *
 * WHY A SOURCE CENSUS AND NOT A RENDER. Every component named here reads
 * assistant-ui composer state (`useAui`, `useAuiState`, `ComposerPrimitive`), so
 * none of them mounts without a runtime, a query client and a live workspace —
 * the same reason `displayed-defaults.test.ts` and `message-attachments.test.ts`
 * are censuses.
 *
 * The defects it guards against all look correct on screen:
 *
 *  - the vendored anatomy adopted but the runtime plumbing quietly dropped with
 *    it (the dropzone, paste-to-attach, the real textarea ref), which reads as a
 *    working composer until somebody drops a file on it;
 *  - a staged row removed BY FILENAME, which silently removes the wrong one of
 *    two files a reader legitimately named the same thing;
 *  - a size invented for a file nobody weighed;
 *  - the expand dialog re-cloned beside the shared one, which is wrong in the
 *    accessibility tree before it is wrong anywhere else.
 */
const THREAD = "components/grove/workspace/thread.tsx";
const ROW = "components/grove/workspace/composer-attachment.tsx";
const SURFACE = "components/grove/workspace/composer.tsx";
const MODEL = "components/grove/workspace/composer-model.tsx";

const VENDORED = "@/components/elements/composer";
const SHARED = "@/components/grove/composer";
const FILE_ROW = "@/components/grove/attachment-file";

/**
 * A file's code with its comments blanked.
 *
 * The absence assertions below are the reason: the developer most likely to
 * write `Dialog`, `attachment.name` or `--composer-bg` is the one explaining why
 * this file no longer does, so a raw scan fails precisely the commit that fixed
 * the defect. The length check is the other half — a census that reports clean
 * after reading nothing is indistinguishable from one that read the real file.
 */
function code(path: string): string {
  const text = readFileSync(path, "utf8");
  expect(text.length, `${path} is empty`).toBeGreaterThan(500);
  return text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

describe("the reply composer is the vendored anatomy", () => {
  it("composes the vendored bar, not a Grove shell", () => {
    const text = code(SURFACE);
    expect(text).toContain(`from "${VENDORED}"`);
    for (const part of ["<Composer ", "<ComposerBar", "<ComposerToolbar", "<ComposerActions"]) {
      expect(text, part).toContain(part);
    }
  });

  it("leaves the thread port with no composer of its own", () => {
    // Every other `Thread` caller is read-only, so a fallback composer there
    // was code no route could mount.
    const text = code(THREAD);
    expect(text).not.toContain("ComposerPrimitive");
    expect(text).not.toContain("--composer-bg");
  });

  it("keeps every runtime capability the vendored div knows nothing about", () => {
    // `ComposerBar` is a div: it does not own the composer scope, the drag
    // target, or the editor. Adopting it while dropping any of these leaves a
    // composer that looks finished and cannot take a file.
    const text = code(SURFACE);
    expect(text).toContain("<ComposerPrimitive.Root");
    expect(text).toContain("<ComposerPrimitive.Input");
    expect(text).toContain("addAttachmentOnPaste");
    expect(text).toContain("ref={inputRef}");
    expect(text).toContain('aria-label="Message input"');
    expect(text).toContain('data-slot="composer-input"');
  });

  it("slots the drag target ONTO the vendored bar, so the drag tint is the bar's", () => {
    // The dropzone sets `data-dragging` on whatever element it renders; a
    // wrapper would make the drop target a different box from the one that
    // reacts, and `dragActive` would be a second copy of the primitive's state.
    const text = code(SURFACE);
    expect(text).toMatch(/<ComposerPrimitive\.AttachmentDropzone asChild>\s*<ComposerBar/);
    expect(text).not.toContain("dragActive");
  });

  it("swaps send for cancel on the runtime's own gate, and keeps dictation", () => {
    const text = code(SURFACE);
    expect(text).toContain("<ComposerPrimitive.Send asChild>");
    expect(text).toContain("<ComposerPrimitive.Cancel asChild>");
    expect(text).toContain("aui-composer-send");
    expect(text).toContain("s.thread.capabilities.dictation");
  });

  it("puts attach alone on the left and everything else against send", () => {
    const text = code(SURFACE);
    const attach = text.indexOf("<ComposerAttachButton");
    const actions = text.indexOf("<ComposerActions", attach);
    const send = text.indexOf("<SendOrCancel");
    expect(attach).toBeGreaterThan(0);
    expect(actions).toBeGreaterThan(attach);
    expect(send).toBeGreaterThan(actions);
    for (const control of ["<WorkspaceContext", "<ComposerModel", "{expandControl}", "<NativeInterrupt"]) {
      const at = text.indexOf(control, actions);
      expect(at, control).toBeGreaterThan(actions);
      expect(at, control).toBeLessThan(send);
    }
  });
});

describe("the unified triggers ride the same bar", () => {
  it("opens `/` and `@` through assistant-ui's trigger primitives", () => {
    const text = code(SURFACE);
    expect(text).toContain("<ComposerPrimitive.Unstable_TriggerPopoverRoot>");
    expect(text).toContain('char="/"');
    expect(text).toContain('char="@"');
    expect(text).toContain("unstable_useSlashCommandAdapter");
    expect(text).toContain("unstable_useMentionAdapter");
  });

  it("delivers a command through the control route, never as typed prose", () => {
    const text = code(SURFACE);
    expect(text).toContain("useInvokeControl");
    expect(text).toContain("removeOnExecute: true");
  });

  it("never lets `@` fall back to the thread's tool list", () => {
    // With no `items` the vendored adapter lists model-context tools instead,
    // which Grove registers none of — an empty menu that looks broken.
    expect(code(SURFACE)).toMatch(/unstable_useMentionAdapter\(\{\s*items\s*\}\)/);
  });
});

describe("a staged file is the shared File row, removed by identity", () => {
  it("draws the shared row rather than a second file vocabulary", () => {
    const text = code(ROW);
    expect(text).toContain(`from "${FILE_ROW}"`);
    expect(text).toContain("<AttachmentFile");
    // The list wrapper is the vendored one; a hand-rolled flex column here is
    // how the two surfaces start spacing their rows differently.
    expect(text).toContain("<ComposerAttachments");
  });

  it("maps the runtime's own status onto the row's three states", () => {
    const text = code(ROW);
    expect(text).toContain('"running"');
    expect(text).toContain('"error"');
    expect(text).toMatch(/state=\{/);
  });

  it("NEVER removes an attachment by filename", () => {
    // Two staged files may legitimately share a name, so a name-keyed remove
    // deletes whichever one the runtime happens to find first — and the reader
    // sees a row disappear, just not the row they clicked. The runtime's own
    // scoped verb is keyed on the attachment's identity.
    const text = code(ROW);
    expect(text).toMatch(/attachment\(\)\.remove\(\)|removeAttachment\(/);
    expect(text).not.toMatch(/remove\w*\(\s*name\b/);
    expect(text).not.toMatch(/\.name\s*===/);
  });

  it("states a size only when the runtime actually holds the file", () => {
    // `size` is the staged File's own byte count while it is staged and the
    // completed part's published count after. The shared row renders nothing
    // for an absent size; synthesising a zero would claim a fact about a file
    // nobody weighed.
    const text = code(ROW);
    expect(text).toContain("file?.size");
    expect(text).toContain("filePartSize(");
    expect(text).not.toMatch(/size[^\n]*\?\?\s*0/);
  });
});

describe("expanding moves the one composer through the shared dialog", () => {
  it("delegates the dialog, the flag and focus restore to the shared component", () => {
    const text = code(SURFACE);
    expect(text).toContain(`from "${SHARED}"`);
    expect(text).toContain("<ExpandedComposer");
    expect(text).toContain('testId="workspace-composer"');
  });

  it("keeps no second dialog, expand flag or focus-restore copy of its own", () => {
    // A local Dialog beside the shared one is two editors for one draft, and a
    // local `onCloseAutoFocus` is a second answer to where focus lands.
    const text = code(SURFACE);
    expect(text).not.toContain("<Dialog");
    expect(text).not.toContain("DialogContent");
    expect(text).not.toContain("onCloseAutoFocus");
    expect(text).not.toMatch(/useState<boolean>|setExpanded/);
  });

  it("titles the dialog something the editor inside it is not already called", () => {
    // Radix names the dialog from its title, so reusing the textarea's own
    // accessible name gives one screen reader two different things called the
    // same thing, nested.
    const text = code(SURFACE);
    expect(text).toMatch(/title="[^"]+"/);
    expect(text).not.toContain('title="Message input"');
  });

  it("still hands the expand control to the toolbar beside the model", () => {
    const text = code(SURFACE);
    expect(text).toContain("expandControl");
    expect(text).toContain("<ComposerModel");
    expect(text).toContain('expandLabel="Expand composer"');
  });
});

describe("the model control keeps its claims while the row gets compact", () => {
  it("still reports the permission mode, and still labels it", () => {
    // The value moved off the toolbar, where a lone lowercase word beside two
    // controls read as decoration, into the menu that owns model-and-session
    // facts. What must not change is that it is REPORTED and NAMED: an
    // unlabelled value is indistinguishable from a model name.
    const text = code(MODEL);
    expect(text).toContain("controls.data?.permission_mode");
    expect(text).toContain('data-testid="composer-permission-mode"');
    expect(text).toContain("Permission mode");
  });

  it("leaves the reported-versus-requested semantics exactly as they were", () => {
    // `displayed-defaults.test.ts` owns these as a rule; they are restated here
    // because the compaction pass is the one most likely to "tidy" them away.
    const text = code(MODEL);
    expect(text).toContain('requested?.id ?? currentModel ?? "Agent default"');
    expect(text).toContain("currentModel !== requested.reportedModel");
    expect(text).toContain("Agent default");
  });
});
