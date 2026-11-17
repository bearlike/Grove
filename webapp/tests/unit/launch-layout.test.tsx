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
/** The composer body moved into its own file so More options can mount it. */
const COMPOSER = "components/grove/launch/launch-composer.tsx";
const DRAFT = "components/grove/launch/launch-draft.tsx";
const FILES = "components/grove/launch/composer-files.tsx";
const ROW = "components/grove/launch/controls/control-row.tsx";
const SUMMARY = "components/grove/launch/launch-summary.tsx";

/** Source position of a needle, failing loudly rather than returning -1. */
function at(source: string, needle: string): number {
  const index = source.indexOf(needle);
  expect(index, `${needle} is absent, so its position cannot be compared`).toBeGreaterThan(-1);
  return index;
}


describe("the greeting", () => {
  const surface = read(SURFACE);

  it("is one row: the small logo beside the question, with no field or grid", () => {
    expect(surface).toContain("What should we work on?");
    expect(surface).not.toContain("Welcome back.");
    expect(surface).not.toContain("launch-brand-field");
    const row = surface.slice(at(surface, "launch-greeting"), at(surface, "What should we work on?"));
    expect(at(row, "<AppLogo")).toBeLessThan(at(row, "<h1"));
    expect(row).toContain("size-9");
  });

  it("leaves the look to the theme: no gradient or mask in the surface", () => {
    expect(surface).not.toMatch(/bg-gradient|mask-image|linear-gradient/);
  });
});

describe("the composer body", () => {
  const surface = read(COMPOSER);

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
  const surface = read(COMPOSER);

  const toolbarOf = (): string =>
    surface.slice(at(surface, "<ComposerToolbar>"), at(surface, "</ComposerBar>"));

  it("puts attach, model and effort left and dictate and send right", () => {
    const toolbar = toolbarOf();
    const order = [
      "<LaunchAttachFiles",
      "<ModelPill",
      "<EffortPill",
      "<LaunchDictateButton",
      "<ComposerSend",
    ].map((needle) => at(toolbar, needle));
    expect([...order].sort((a, b) => a - b)).toEqual(order);
  });

  it("never narrates the workspace name under the composer", () => {
    // "The agent names this workspace once it starts." appeared the moment a
    // brief was typed — prose where the reader wanted nothing.
    expect(surface).not.toContain("LaunchDerivedTitle");
    expect(surface).not.toContain("names this workspace");
  });

  it("keeps expand out of the toolbar and in the shelf, withheld while dictating", () => {
    expect(toolbarOf()).not.toContain("expandControl");
    // Expanding remounts the composer. The dictation hook lives ABOVE that
    // boundary so the recording survives, and the control is still withheld
    // for its span so the editor cannot vanish mid-sentence.
    expect(surface.slice(at(surface, "composer-shelf"))).toContain("{voice ? null : expandControl}");
    // Above BOTH moves now: the shell-level draft owns it, so neither the
    // expand dialog nor More options can drop a recording.
    expect(read(DRAFT)).toContain("useLaunchDictation(launch.setPrompt, launch.submit)");
    expect(surface).not.toContain("useLaunchDictation(");
  });

  it("gives every control a hover tip wrapped AROUND it, never merged into it", () => {
    const toolbar = toolbarOf();
    expect(toolbar).toContain(`<LaunchTip label="Attach files">`);
    // A pill's tip is withheld while its own menu is open, or the two stack.
    expect(toolbar).toContain(`<LaunchTip label="Model" suppressed={openKind === "model"}>`);
    expect(toolbar).toContain(`<LaunchTip label="Reasoning effort" suppressed={openKind === "effort"}>`);
    expect(toolbar).toContain("<LaunchTip label={launch.isPending");
    // `TooltipTrigger asChild` overwrites the child's `data-slot`, which the
    // composer theme keys on, so the trigger is a wrapper span.
    const tip = read("components/grove/launch/launch-tip.tsx");
    expect(tip).toContain("<span className=");
  });

  it("swaps editor AND toolbar for the voice view, keeping the bar and chips", () => {
    const bar = surface.slice(at(surface, "<ComposerBar"), at(surface, "</ComposerBar>"));
    const voice = at(bar, "<LaunchVoiceView");
    expect(at(bar, "<LaunchAttachmentChips")).toBeLessThan(voice);
    expect(voice).toBeLessThan(at(bar, "<Textarea"));
    expect(voice).toBeLessThan(at(bar, "<ComposerToolbar>"));
  });

  it("sizes nothing itself — the vendored bar and the theme own the geometry", () => {
    // One size for every icon control on a toolbar is a theme rule keyed by
    // slot, so neither composer restates a send size or a bar inset here.
    expect(surface).not.toContain("size-[28px]");
    expect(surface).not.toMatch(/padding:\s*11/);
  });
});

describe("the configuration shelf", () => {
  const surface = read(COMPOSER);
  const row = read(ROW);

  it("sits OUTSIDE the writing surface, after the body", () => {
    // Writing and configuring are different acts. Inside the bar the pills
    // competed with the brief for the same paper and pushed Send around as
    // they wrapped; a sibling shelf under it lets the bar be one thing.
    const bar = at(surface, "</ComposerBar>");
    const shelf = at(surface, "composer-shelf");
    expect(bar).toBeLessThan(shelf);
  });

  it("connects to the bar: the composer column carries no gap or shelf margin", () => {
    const column = surface.slice(at(surface, "<Composer\n"), at(surface, "<ComposerBar"));
    expect(column).not.toMatch(/\bgap-\d/);
    const shelf = surface.slice(at(surface, "composer-shelf"));
    expect(shelf.slice(0, 80)).not.toMatch(/\bm[xytb]?-\d/);
  });



  it("orders the shelf as project/location, runtime, then agent/profile", () => {
    // Each group narrows the next: where the work lands, where it runs, then
    // which tool and profile execute it.
    const order = [
      "<ProjectPill",
      "<WorkingDirectoryPill",
      "<BranchPill",
      "<NodePill",
      "<RuntimePill",
      "<AgentPill",
      "<ProfilePill",
    ].map((needle) => at(row, needle));
    expect([...order].sort((a, b) => a - b)).toEqual(order);
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
    const composer = read(COMPOSER);
    expect(at(composer, "<ModelPill")).toBeGreaterThan(-1);
    expect(at(composer, "composer-shelf")).toBeGreaterThan(-1);
  });

  it("leaves the row a consumer of that group rather than a second one", () => {
    expect(row).not.toContain("LaunchPillGroup");
  });
});

describe("the draft's seed effect", () => {
  const row = read("components/grove/launch/launch-state.tsx");

  it("still seeds the cascade's answers, exactly once", () => {
    // It moved from the control row into the draft's provider, because More
    // options composes the same state without that row. It is the only caller
    // of `seed`, and its dependency list is the fix for the render loop the
    // reducer documents — a second `useEffect` here is the bug returning.
    // Scoped to the seed component: the previewer beside it has effects of its
    // own, and what this pins is ONE effect calling `seed`.
    const seeder = row.slice(row.indexOf("function LaunchDefaultsSeed"), row.indexOf("const PREVIEW_DEBOUNCE_MS"));
    expect(seeder.match(/useEffect\(/g) ?? []).toHaveLength(1);
    expect(row.match(/\bseed\(\s*\{/g) ?? []).toHaveLength(1);
    expect(row).toContain("useWorkspaceDefaults(values.repoRoot)");
    // `agentName` re-seeds the inherited model for the agent now selected.
    expect(row).toContain("[defaults.data, seed, agentName, repo, engineProfilePending]");
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
    expect(row).toContain("model: engineProfilePending ? undefined : inheritedModel(resolved, ");
  });

  it("still names the region the e2e suite and the pickers address", () => {
    expect(read(ROW)).toContain("LAUNCH_TESTIDS.controls");
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
    const composer = read(COMPOSER);
    const editor = composer.slice(at(composer, "<Textarea"));
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
      "LAUNCH_TESTIDS.footerLinks",
      "LAUNCH_TESTIDS.error",
      "LAUNCH_TESTIDS.refusal",
    ]) {
      expect(surface + read(COMPOSER) + read(SUMMARY), `${id} lost its region`).toContain(id);
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

  it("keeps refusal and error distinct inside the shelf note", () => {
    // A refusal is "nothing was attempted"; an error is "the create failed".
    // The note owns both, so no bare error paragraph can compete beneath it.
    const summary = read(SUMMARY);
    expect(summary).toContain("launch.refusal");
    expect(summary).toContain("launch.error");
    expect(summary).toContain("Couldn’t create workspace.");
    expect(surface).not.toContain("launch.refusal");
    expect(surface).not.toContain("launch.error");
  });

  it("still opens the full form for everything the shelf does not carry", () => {
    // Brief, base ref, skip-init and save-as-defaults have no pill and are not
    // supposed to get one. `More options` is where they live and it is the
    // reason the shelf can stay short.
    expect(surface).toContain("More options");
    expect(surface).toContain("openMoreOptions(true)");
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
