import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

/**
 * The workspace reply composer, after the shared shell took over its paper.
 *
 * WHY A SOURCE CENSUS AND NOT A RENDER. Every component named here reads
 * assistant-ui composer state (`useAui`, `useAuiState`, `ComposerPrimitive`), so
 * none of them mounts without a runtime, a query client and a live workspace —
 * the same reason `displayed-defaults.test.ts` and `message-attachments.test.ts`
 * are censuses. What the shared atoms themselves render is pinned by
 * `shared-composer.test.tsx`, which CAN render them; what is pinned here is the
 * part only this seam can get wrong.
 *
 * The defects it guards against all look correct on screen:
 *
 *  - the shared shell adopted but the runtime plumbing quietly dropped with it
 *    (the dropzone, paste-to-attach, the real textarea ref), which reads as a
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

/** The `WorkspaceComposer` body alone, so a thread-level delta cannot answer for it. */
function workspaceComposer(): string {
  const text = code(THREAD);
  const start = text.indexOf("export function WorkspaceComposer(");
  expect(start, "WorkspaceComposer is not exported from the thread port").toBeGreaterThan(0);
  const end = text.indexOf("\nconst MessageError", start);
  expect(end, "the composer section has no end marker").toBeGreaterThan(start);
  return text.slice(start, end);
}

describe("the reply composer's paper is the shared one", () => {
  it("renders the shared body instead of a hand-rolled shell", () => {
    const text = workspaceComposer();
    expect(code(THREAD)).toContain(`from "${SHARED}"`);
    expect(text).toContain("<ComposerBody");
    // The slot the old hand-rolled box carried. Its absence is the whole point:
    // the theme layer reaches the bar through the VENDORED slot, and a Grove
    // box drawing its own surface opted this surface out of it silently.
    expect(text).not.toContain("aui_composer-shell");
  });

  it("stops re-declaring the composer custom properties the theme now owns", () => {
    // Three `--composer-*` values were restated on the shell because a dialog
    // portal leaves the thread's inheritance tree. The shared bar carries its
    // own surface, so restating them here would be this file having an opinion
    // about a look it no longer owns — and the two copies would drift.
    const text = workspaceComposer();
    expect(text).not.toContain("--composer-bg");
    expect(text).not.toContain("--composer-radius");
    expect(text).not.toContain("--composer-padding");
  });

  it("keeps every runtime capability the shared shell knows nothing about", () => {
    // The shared body is a div: it does not own the composer scope, the drag
    // target, or the editor. Adopting it while dropping any of these leaves a
    // composer that looks finished and cannot take a file.
    const text = workspaceComposer();
    expect(text).toContain("<ComposerPrimitive.Root");
    expect(text).toContain("<ComposerPrimitive.AttachmentDropzone asChild>");
    expect(text).toContain("<ComposerPrimitive.Input");
    expect(text).toContain("addAttachmentOnPaste");
    expect(text).toContain("ref={inputRef}");
    expect(text).toContain('aria-label="Message input"');
  });

  it("names the editor with the vendored input slot the theme styles", () => {
    // `ComposerPrimitive.Input` spreads native textarea props, so the slot is
    // reachable from the call site — and a class-only hook would leave this one
    // editor outside the focus and placeholder rules both surfaces share.
    expect(workspaceComposer()).toContain('data-slot="composer-input"');
  });

  it("slots the drag target ONTO the shared bar, so the drag tint is the bar's", () => {
    // The dropzone sets `data-dragging="true"` on whatever element it renders,
    // so `asChild` onto the shared body is what puts that attribute on the
    // themed slot. A wrapper div around the bar instead would make the drop
    // target a different element from the one that can show it, and the shared
    // `dragActive` prop is NOT the answer: it would be a second copy of state
    // the primitive already publishes, readable only from inside it.
    const text = workspaceComposer();
    expect(text).toMatch(/<ComposerPrimitive\.AttachmentDropzone asChild>\s*<ComposerBody/);
    expect(text).not.toContain("dragActive");
  });

  it("keeps the measured 28px send, its class and its runtime gate", () => {
    // `tests/e2e/sharp-surfaces.spec.ts` measures `.aui-composer-send` at 28px
    // with a 12px corner inset, and the inset is the bar's 11px padding plus its
    // 1px border — so the button's own size is the half that lives here.
    const text = workspaceComposer();
    expect(text).toContain("aui-composer-send size-[28px]");
    expect(text).toContain("<ComposerPrimitive.Send asChild>");
    expect(text).toContain("<ComposerSend");
    expect(text).toContain("<ComposerAttachButton");
    expect(text).toContain("<ComposerPrimitive.Cancel asChild>");
    expect(text).toContain("s.thread.capabilities.dictation");
  });
});

describe("the action row is the vendored toolbar, attach left and the rest right", () => {
  it("composes the native toolbar and action groups", () => {
    const text = workspaceComposer();
    expect(text).toContain("<ComposerToolbar");
    expect(text).toContain("<ComposerActions");
  });

  it("puts attach alone on the left and everything else on the right", () => {
    // Both surfaces read the same way round: one quiet add-file verb at the
    // start of the row, and every control that answers "how will this be sent"
    // grouped against the send button it affects.
    const text = workspaceComposer();
    const attach = text.indexOf("<ComposerAttachButton");
    const injected = text.indexOf("{children}");
    const send = text.indexOf("ComposerPrimitive.Send");
    expect(attach).toBeGreaterThan(0);
    expect(injected).toBeGreaterThan(attach);
    expect(send).toBeGreaterThan(injected);
    expect(text.slice(attach, injected)).toContain("</ComposerActions>");
    expect(text.slice(attach, injected)).toContain("<ComposerActions");
    expect(text.slice(injected, send)).not.toContain("</ComposerActions>");
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
    expect(text).not.toContain("useState");
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
