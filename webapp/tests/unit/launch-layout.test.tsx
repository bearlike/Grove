import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { LAUNCH_TESTIDS } from "@/components/grove/launch/launch-state";

/**
 * The landing composer's ANATOMY, pinned where the anatomy actually lives.
 *
 * Every rule here is a relationship BETWEEN two elements — chips above the
 * editor, the shelf after the body, one pill group around both — and a
 * relationship is exactly what no single component can assert about itself.
 * The surface needs a query client, a fleet stream and a popover root to
 * render, so there is no artifact this `node`-environment suite could inspect
 * instead; source assertions in the style of `launch-control-row.test.ts` are
 * the seam that is available.
 *
 * ORDER IS ASSERTED BY SOURCE POSITION, which is sound here for one reason:
 * every element below is a sibling in one JSX tree with no conditional
 * reordering, so "appears earlier in the file" and "paints earlier in the
 * document" are the same statement.
 */
const read = (path: string): string => readFileSync(path, "utf8");

const SURFACE = "components/grove/launch/launch-surface.tsx";
const FILES = "components/grove/launch/composer-files.tsx";
const ROW = "components/grove/launch/controls/control-row.tsx";

/** Source position of a needle, failing loudly rather than returning -1. */
function at(source: string, needle: string): number {
  const index = source.indexOf(needle);
  expect(index, `${needle} is absent, so its position cannot be compared`).toBeGreaterThan(-1);
  return index;
}

describe("the welcome block", () => {
  const surface = read(SURFACE);

  it("greets, then asks — two tiers, not one heading doing both jobs", () => {
    // A single line carrying both the greeting and the question is the shape
    // this replaced: it made the question compete with the greeting for the
    // one emphasis the page has. The tiers are the design system's own
    // (§ heading `text-2xl`, supporting sentence `text-base` secondary), so
    // this pins the PAIRING rather than inventing a scale.
    expect(surface).toContain("Welcome back.");
    expect(surface).toContain("What would you like to build and improve today?");
    expect(at(surface, "Welcome back.")).toBeLessThan(
      at(surface, "What would you like to build and improve today?"),
    );
  });

  it("keeps the greeting at text-2xl and its question at text-base secondary", () => {
    // The greeting's classes are authored BEFORE its testid, so the window is
    // the whole `<h1>` rather than the gap between the two.
    const greeting = surface.slice(at(surface, "<h1"), at(surface, "Welcome back."));
    expect(greeting).toContain("text-2xl");
    const question = surface.slice(
      at(surface, "Welcome back."),
      at(surface, "What would you like to build and improve today?"),
    );
    expect(question).toContain("text-base");
    expect(question).toContain("text-content-secondary");
  });

  it("puts the decorative field BEHIND the mark, and hides it from the a11y tree", () => {
    // The field is texture: it says nothing a screen reader could use, and it
    // must never take a pointer event away from the controls layered over it.
    // Announcing it would put a nameless region between the brand and the
    // heading, which is the one place a landing page cannot afford noise.
    // `aria-hidden` precedes the class on the same element, so the window
    // opens at the span rather than at the class name.
    const field = surface.slice(at(surface, "<span aria-hidden"), at(surface, "<AppLogo"));
    expect(field).toContain("launch-brand-field");
    expect(at(surface, "launch-brand-field")).toBeLessThan(at(surface, "<AppLogo"));

    // The field covers the brand AND the headline, so it must swallow no
    // clicks. That is asserted in the THEME rather than here on purpose:
    // `pointer-events` is not a per-caller variant of this decoration, it is
    // what the decoration IS, and a utility class at one call site would let
    // the next call site omit it.
    const theme = read("app/globals.css");
    const rule = theme.slice(at(theme, ".launch-brand-field {"));
    expect(rule.slice(0, rule.indexOf("}"))).toContain("pointer-events: none");
  });

  it("gives the field a BOX, because the theme deliberately gives it none", () => {
    // The seam between this file and the theme, and the one that actually
    // broke: `.launch-brand-field` is `position`-less and sized only by its
    // `closest-side` mask, so an absolutely-positioned span with no offsets
    // collapses to 0×0 and paints nothing. Every gate stays green — the class
    // is present, the CSS is valid, the a11y tree is correct — and the texture
    // is simply invisible. Neither side can see this alone: the theme cannot
    // know the caller's measure, and the caller reads a class that looks
    // complete.
    const field = surface.slice(at(surface, "<span aria-hidden"), at(surface, "<AppLogo"));
    expect(field).toMatch(/\b(?:-?inset|-?top|-?bottom|-?left|-?right|w-|h-|size-)/);
    const theme = read("app/globals.css");
    const rule = theme.slice(at(theme, ".launch-brand-field {"));
    expect(rule.slice(0, rule.indexOf("}"))).not.toMatch(/\bwidth:|\bheight:/);
  });

  it("leaves the CSS to the theme — the surface names the class and stops", () => {
    // `launch-brand-field` is a hook, not a look. If this file ever grows the
    // gradient itself, the field and every other decorative surface drift
    // apart, which is the drift `lint:styling` exists to prevent.
    const field = surface.slice(at(surface, "launch-brand-field"), at(surface, "<AppLogo"));
    expect(field).not.toMatch(/bg-gradient|mask-image|linear-gradient/);
  });
});

describe("the composer body", () => {
  const surface = read(SURFACE);

  it("writes the brief in the vendored Textarea, not a hand-styled one", () => {
    // The previous answer was a raw `<textarea>` carrying its own padding,
    // placeholder colour and focus treatment — a look invented beside the
    // vendored one rather than composed from it. `components/ui/textarea` is
    // the approved multi-line editor and the reason this surface still mounts
    // no assistant-ui runtime.
    expect(surface).toContain('from "@/components/ui/textarea"');
    expect(surface).toContain("<Textarea");
    expect(surface).not.toMatch(/<textarea\b/);
  });

  it("stacks chips ABOVE the editor and the toolbar BELOW it", () => {
    // Both composers now read top-to-bottom as: what is attached, what you are
    // writing, what you can do. The landing surface used to put its file list
    // under the brief, which was defensible when the list was full-width rows
    // and indefensible now that it is chips — the same anatomy in two places
    // must not be two different orders.
    const attachments = at(surface, "<LaunchAttachmentChips");
    const editor = at(surface, "<Textarea");
    const toolbar = at(surface, "<ComposerToolbar");
    expect(attachments).toBeLessThan(editor);
    expect(editor).toBeLessThan(toolbar);
  });

  it("keeps Enter/Shift+Enter/IME and clipboard files on the editor", () => {
    // These four are the composer's whole keyboard contract and they moved
    // element. A pasted-in Textarea that dropped `isComposing` would submit a
    // half-typed Japanese character as a task brief, and nothing about that
    // failure points back at this change.
    const keys = surface.slice(at(surface, "onKeyDown="), at(surface, "placeholder="));
    expect(keys).toContain("event.shiftKey");
    expect(keys).toContain("event.nativeEvent.isComposing");
    expect(keys).toContain("launch.submit()");
    expect(surface).toContain("enterKeyHint=");
    expect(surface).toContain('aria-label="Task brief"');
  });
});

describe("the main toolbar", () => {
  const surface = read(SURFACE);

  it("puts attach on the left and model, expand and send on the right", () => {
    // The split is by KIND: attaching is an input to the message, the three on
    // the right act on it. `ComposerActions` is the vendored right-hand group,
    // so everything inside it lands in the corner the send button already owns.
    const toolbar = surface.slice(at(surface, "<ComposerToolbar"));
    const attach = at(toolbar, "<LaunchAttachFiles");
    const actions = at(toolbar, "<ComposerActions");
    const model = at(toolbar, "<ModelPill");
    // The expand button is the shared dialog's own control, handed down as
    // `expandControl` and already carrying `launch-expand`. This surface
    // decides only WHERE it sits — between the model pill and send — which is
    // the half of the arrangement it still owns.
    const expand = at(toolbar, "{expandControl}");
    const send = at(toolbar, "<ComposerSend");
    expect(attach).toBeLessThan(actions);
    expect(actions).toBeLessThan(model);
    expect(model).toBeLessThan(expand);
    expect(expand).toBeLessThan(send);
  });

  it("sizes nothing itself — the vendored bar and the theme own the geometry", () => {
    // One size for every icon control on a toolbar is a theme rule keyed by
    // slot, so neither composer restates a send size or a bar inset here.
    expect(surface).not.toContain("size-[28px]");
    expect(surface).not.toMatch(/padding:\s*11/);
  });
});

describe("the configuration shelf", () => {
  const surface = read(SURFACE);
  const row = read(ROW);

  it("sits OUTSIDE the writing surface, after the body", () => {
    // Writing and configuring are different acts. Inside the bar the pills
    // competed with the brief for the same paper and pushed Send around as
    // they wrapped; a sibling shelf under it lets the bar be one thing.
    const bar = at(surface, "</ComposerBar>");
    const shelf = at(surface, "composer-shelf");
    expect(bar).toBeLessThan(shelf);
  });

  it("insets the shelf from the body's edges rather than spanning them", () => {
    // `mx-3` reads the shelf as subordinate to the bar above it. Flush edges
    // would make it a second bar of equal weight.
    const shelf = surface.slice(at(surface, "composer-shelf"));
    expect(shelf.slice(0, 200)).toContain("mx-3");
  });

  it("orders the shelf WHERE the work happens, then leaves runtime on the right", () => {
    const project = at(row, "<ProjectPill");
    const directory = at(row, "<WorkingDirectoryPill");
    const agent = at(row, "<AgentPill");
    const branch = at(row, "<BranchPill");
    const runtime = at(row, "<RuntimePill");
    expect(project).toBeLessThan(directory);
    expect(directory).toBeLessThan(agent);
    expect(agent).toBeLessThan(branch);
    expect(branch).toBeLessThan(runtime);
  });

  it("keeps the working directory REACHABLE beside the project it belongs to", () => {
    // The mechanism may be de-emphasised; it may not disappear. A project's
    // `agent_cwds` are the difference between an agent starting at the repo
    // root and starting in the one subdirectory that builds, and `More
    // options` opening a nine-field modal is not a substitute for the control.
    expect(row).toContain("<WorkingDirectoryPill");
  });

  it("hands the model pill to the toolbar, so the shelf is not two rows again", () => {
    // The shelf holds what configures the WORKSPACE; the model configures the
    // message's agent and belongs with send. Leaving it here is what forced
    // the authored two-row split this replaced.
    expect(row).not.toContain("<ModelPill");
    expect(row).not.toContain("ModelPill");
  });
});

describe("one pill group around the whole composer", () => {
  const surface = read(SURFACE);
  const row = read(ROW);

  it("opens the group above BOTH the toolbar and the shelf", () => {
    // `LaunchPillGroup` holds a single `openKind`, and that single key is the
    // exclusion rule: opening the model menu must close the project menu.
    // Two groups would give the two halves of one composer independent open
    // states and let two popovers stack over each other.
    //
    // The group encloses `ExpandedComposer`, which renders the composer in
    // BOTH mount points — so the enclosure has to be asserted against that
    // element rather than against the pills, which live one callback deeper.
    expect(surface).toContain("<LaunchPillGroup>");
    const open = at(surface, "<LaunchPillGroup>");
    const close = at(surface, "</LaunchPillGroup>");
    expect(open).toBeLessThan(at(surface, "<ExpandedComposer"));
    expect(close).toBeGreaterThan(at(surface, "</ExpandedComposer>"));
    // And the composer that callback builds carries both halves, so one group
    // spans the model pill and the shelf however the tree is nested.
    const composer = surface.slice(at(surface, "function LaunchComposer"));
    expect(at(composer, "<ModelPill")).toBeGreaterThan(-1);
    expect(at(composer, "composer-shelf")).toBeGreaterThan(-1);
  });

  it("leaves the row a consumer of that group rather than a second one", () => {
    expect(row).not.toContain("LaunchPillGroup");
  });
});

describe("the control row's seed effect", () => {
  const row = read(ROW);

  it("still seeds the cascade's answers, exactly once", () => {
    // The row shrank; the effect did not move. It is the only caller of
    // `seed`, and its dependency list is the fix for the render loop the
    // reducer documents — a second `useEffect` here is the bug returning.
    expect(row.match(/useEffect\(/g) ?? []).toHaveLength(1);
    expect(row).toContain("useWorkspaceDefaults(values.repoRoot)");
    expect(row).toContain("[defaults.data, seed]");
    for (const field of [
      "agentName",
      "runtime",
      "brief",
      "model",
      "branchMode",
      "baseRef",
      "skipInit",
    ]) {
      expect(row, `${field} dropped out of the seed`).toContain(`${field}:`);
    }
  });

  it("keeps seeding the MODEL even though the pill left this file", () => {
    // The pill moved, the default did not. `resolve_models` answers per agent
    // and that answer is what the model pill displays wherever it renders; a
    // seed trimmed to "the fields this file still draws" would leave the
    // toolbar's pill permanently unfilled.
    expect(row).toContain("model: resolved.model");
  });

  it("still names the region the e2e suite and the pickers address", () => {
    expect(row).toContain("LAUNCH_TESTIDS.controls");
  });
});

describe("the expanded brief", () => {
  const surface = read(SURFACE);

  it("moves the ONE composer into the shared dialog instead of cloning it", () => {
    // The draft, every control value and the attachment list live above this
    // component, which is the only reason a remount is free. Rendering a
    // second copy would give one brief two tab stops and two accessible names
    // called "Task brief". `ExpandedComposer` takes the composer as a render
    // callback precisely so there is one call site and no second branch that
    // could drift into a clone.
    expect(surface).toContain("<ExpandedComposer");
    expect(surface).toContain('testId="launch"');
  });

  it("drops the hand-assembled dialog now that the shared one exists", () => {
    // The duplicate this deletes: a `DialogContent` with its own height,
    // width, header and description, maintained here in parallel with the
    // session composer's. One dialog, one owner.
    expect(surface).not.toContain("DialogContent");
    expect(surface).not.toContain("DialogHeader");
    expect(surface).not.toContain('from "@/components/ui/dialog"');
  });

  it("binds the editor to the ref the shared dialog restores focus through", () => {
    // Radix restores focus to the element that opened the dialog, and that
    // element is inside the composer the dialog just unmounted — so focus
    // falls to the body unless something puts it back. `ExpandedComposer`
    // owns that handoff and hands down the ref it will focus; this surface's
    // only obligation is to attach it to the editor. An unattached ref fails
    // silently: the dialog closes, the focus call finds `null`, and only a
    // keyboard user ever notices.
    const editor = surface.slice(at(surface, "<Textarea"));
    expect(editor.slice(0, 200)).toContain("ref={inputRef}");
  });
});

describe("staged files draw as the shared File row", () => {
  const files = read(FILES);

  it("composes the shared row rather than a second file vocabulary", () => {
    // The landing page, the session composer and a SENT turn draw a file the
    // same way, through one composition over the vendored `File` element.
    expect(files).toContain("AttachmentFile");
    expect(files).toContain("ComposerAttachments");
    expect(files).not.toContain("File.Root");
    expect(files).not.toContain("ComposerAttachmentChip");
  });

  it("removes by INDEX, because two staged files may share a name", () => {
    // The native chip hands its own `name` back to `onRemove`, and a name is
    // not an identity: picking `notes.txt` from two directories stages two
    // valid rows, and removing by name would drop the wrong one — or both.
    // The callback closes over the index instead and ignores the argument.
    expect(files).toContain("onRemove={() => onRemove(index)}");
  });

  it("still tells the user which files the browser could not restore", () => {
    // `pendingReAdd` is the one state with no bytes behind it: a draft
    // recovered after a navigation knows the names and cannot know the
    // contents. Silently dropping those names makes a restored draft look
    // complete when it is not.
    expect(files).toContain("pendingReAdd");
    expect(files).toContain("Re-add file");
  });

  it("names the region, and keeps the picker's own testid", () => {
    expect(files).toContain("LAUNCH_TESTIDS.attachments");
    expect(files).toContain("LAUNCH_TESTIDS.attach");
  });
});

describe("what must not change", () => {
  const surface = read(SURFACE);

  it("keeps every region name the e2e suite addresses", () => {
    // A redesign that renames a testid turns a real regression into a test
    // that cannot run. Listed by name rather than counted, so a deleted region
    // fails on the region rather than on an arity.
    for (const id of [
      "LAUNCH_TESTIDS.page",
      "LAUNCH_TESTIDS.brand",
      "LAUNCH_TESTIDS.headline",
      "LAUNCH_TESTIDS.composer",
      "LAUNCH_TESTIDS.input",
      "LAUNCH_TESTIDS.derivedTitle",
      "LAUNCH_TESTIDS.footerLinks",
      "LAUNCH_TESTIDS.error",
      "LAUNCH_TESTIDS.refusal",
    ]) {
      expect(surface, `${id} lost its region`).toContain(id);
    }
  });

  it("still emits launch-expand and launch-expanded, now via the shared testId", () => {
    // These two moved OWNER rather than disappearing: the shared dialog
    // derives both from `testId`, so the surface passing the wrong prefix
    // would leave the browser suite querying testids that nothing emits — a
    // failure that reads as "the dialog is broken" instead of "the name
    // changed". Pinned as the derivation, at both ends.
    expect(surface).toContain('testId="launch"');
    expect(LAUNCH_TESTIDS.expand).toBe("launch-expand");
    expect(LAUNCH_TESTIDS.expanded).toBe("launch-expanded");
    const shared = read("components/grove/composer.tsx");
    expect(shared).toContain("`${testId}-expand`");
    expect(shared).toContain("`${testId}-expanded`");
  });

  it("keeps the create request's refusal and error slots distinct", () => {
    // A refusal is "nothing was attempted"; an error is "the create failed".
    // Collapsing them would put "Couldn't create workspace" in front of a
    // decision the user has not made yet.
    expect(surface).toContain("launch.refusal");
    expect(surface).toContain("launch.error");
    expect(surface).toContain("Couldn’t create workspace.");
  });

  it("still opens the full form for everything the shelf does not carry", () => {
    // Brief, base ref, skip-init and save-as-defaults have no pill and are not
    // supposed to get one. `More options` is where they live and it is the
    // reason the shelf can stay short.
    expect(surface).toContain("More options");
    expect(surface).toContain("openCreate(values.repoRoot ?? \"\")");
  });

  it("adds no runtime and no backend call to this route", () => {
    // The absence that pays for this surface's navigation working at all:
    // mounting `AssistantRuntimeProvider` here is what silently broke every
    // client-side navigation away from `/`.
    //
    // Asserted against the IMPORT LIST, not the file text. The docstring above
    // `LaunchComposer` names both symbols while explaining why neither is
    // used, so a raw search fails on the comment that documents compliance —
    // and would keep failing however the code was written.
    const imports = surface.slice(0, at(surface, "export function LaunchSurface"));
    expect(imports).not.toContain("AssistantRuntimeProvider");
    expect(imports).not.toContain("ComposerPrimitive");
    expect(imports).not.toContain("@assistant-ui/react");
  });
});
