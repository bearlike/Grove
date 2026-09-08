import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  LaunchPill,
  LaunchPillGroup,
  capturesCommandKey,
} from "@/components/grove/launch/control-pill";

/**
 * The launch pickers are the vendored `ModelSelector`, composed six times.
 *
 * Reusing one component for six controls is right, and it has one failure mode
 * that nothing detects: the vendored DEFAULTS are written for the single
 * control it was built for. `ModelSelectorContent`'s own body hard-codes a
 * keyboard anchor called "Model", a `"Search models..."` placeholder and a
 * `"No models found."` empty state — so the Agent picker announced itself as
 * Model, and narrowing the Project list to nothing reported an absence of
 * models. Every one of those renders and looks completely normal.
 *
 * Two kinds of assertion below, for two kinds of artifact. The trigger is real
 * SSR output, because it is markup a server produces. The popover BODY is not:
 * Radix portals its content and only mounts it while open, so an SSR render
 * sees nothing at all — which would make a "clean" result mean "the popover was
 * closed", indistinguishable from a pass. Those are pinned as a source census
 * in `launch-control-row.test.ts`'s style, and the keyboard rule is pinned
 * against the pure function that decides it.
 */
const CONTROLS_DIR = "components/grove/launch/controls";
const CONTROL_PILL = "components/grove/launch/control-pill.tsx";

function source(path: string): string {
  const text = readFileSync(path, "utf8");
  // The absence-check rule: a census over an empty file reports clean exactly
  // like one that read the real thing.
  expect(text.length, `${path} is empty`).toBeGreaterThan(500);
  return text;
}

/** Strip comments, so prose EXPLAINING a rejected pattern cannot fail a scan. */
function code(path: string): string {
  return source(path)
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "");
}

function renderPill(props: Partial<React.ComponentProps<typeof LaunchPill>> = {}): string {
  return renderToStaticMarkup(
    <LaunchPillGroup>
      <LaunchPill
        kind="model"
        ariaLabel="Model"
        value="sonnet"
        options={[{ id: "sonnet", label: "Sonnet" }]}
        onSelect={() => undefined}
        {...props}
      />
    </LaunchPillGroup>,
  );
}

describe("the pill trigger", () => {
  it("names the CONTROL and shows the VALUE, so a screen reader hears both", () => {
    const html = renderPill({ kind: "agent", ariaLabel: "Agent", value: "claude", options: [
      { id: "claude", label: "Claude Code" },
    ] });

    expect(html).toContain('aria-label="Agent"');
    expect(html).toContain("Claude Code");
  });

  it("carries the full value in its title, because the visible text truncates", () => {
    // A pill clips rather than wraps, and truncation with no way back to the
    // value is data loss (design system §overflow).
    const html = renderPill({
      value: "anthropic-claude-opus-5-1-extended-context",
      options: [
        { id: "anthropic-claude-opus-5-1-extended-context", label: "Anthropic Claude Opus 5.1 (1M)" },
      ],
    });

    expect(html).toContain("title=\"Model: Anthropic Claude Opus 5.1 (1M)\"");
    expect(html).toContain("truncate");
  });

  it("bounds its own width, so one long project name cannot re-flow the row", () => {
    // Without a cap the trigger is sized by its content, so the row's wrap
    // point moved as `/defaults` and the fleet snapshot landed — the controls
    // visibly rearranged themselves under the user a second after first paint.
    expect(renderPill()).toContain("max-w-44");
  });

  it("uses the visible outline variant rather than a bare ghost trigger", () => {
    // All six launch controls share this trigger. The bounded edge is therefore
    // one family decision, not a model-only exception.
    expect(renderPill()).toContain('data-variant="outline"');
  });

  it("states a REFUSAL instead of a value when the control cannot be used", () => {
    const html = renderPill({ disabledReason: "Choose a project first" });

    expect(html).toContain("Choose a project first");
    expect(html).toContain("disabled");
  });

  it("keeps an invalid answer's REASON on the closed trigger", () => {
    // The custom model id and the branch name are typed INSIDE the popover, so
    // their error messages unmount with it. Send is disabled while either is
    // invalid, and a disabled Send whose explanation is behind a menu you have
    // to reopen is the defect that trade would otherwise buy.
    const html = renderPill({ error: "A custom model id cannot start with a dash." });

    expect(html).toContain("A custom model id cannot start with a dash.");
    expect(html).toContain('aria-invalid="true"');
  });

  it("shows the resolved value rather than the option that opened its editor", () => {
    const html = renderPill({
      value: "-custom",
      options: [{ id: "-custom", label: "Custom…", opensPanel: true }],
      fallbackLabel: "provider/custom-model",
    });
    expect(html).toContain("provider/custom-model");
    expect(html).not.toContain("Custom…");
  });

  it("marks nothing invalid when the answer is fine", () => {
    // Guard against the assertion above passing because `aria-invalid` is
    // simply always present.
    expect(renderPill()).not.toContain('aria-invalid="true"');
  });
});

/**
 * A FIELD INSIDE cmdk'S ROOT LOSES ITS KEYBOARD TO THE LIST.
 *
 * `Command` binds its navigation on the root element, so every keystroke in a
 * nested `<input>` bubbles into it. Enter is the expensive one: cmdk calls
 * `preventDefault()` and dispatches `cmdk-item-select` on the highlighted row,
 * so pressing Enter after typing a branch name re-answered the control with
 * whichever mode happened to be highlighted. Home and End jumped the list
 * rather than moving the caret, and the vertical arrows moved the highlight.
 *
 * Pinned against the predicate rather than through a rendered popover, because
 * the popover needs a DOM and a real key event — and a test that reached the
 * right outcome through cmdk being absent would pass with the guard deleted.
 */
describe("capturesCommandKey", () => {
  it("claims every key cmdk's root acts on", () => {
    for (const key of ["Enter", "Home", "End", "ArrowUp", "ArrowDown"]) {
      expect(capturesCommandKey({ key, ctrlKey: false }), key).toBe(true);
    }
  });

  it("claims the vim bindings, which cmdk enables by DEFAULT", () => {
    // `vimBindings` defaults to true, so Ctrl+N/J/P/K move the list even though
    // nothing in the composition asks for them.
    for (const key of ["n", "j", "p", "k"]) {
      expect(capturesCommandKey({ key, ctrlKey: true }), `ctrl+${key}`).toBe(true);
      // The bare letter is someone typing a branch name.
      expect(capturesCommandKey({ key, ctrlKey: false }), key).toBe(false);
    }
  });

  it("LEAVES ESCAPE ALONE — it is the only keyboard way out of the popover", () => {
    // Radix's dismiss layer listens on `document`, and React's
    // `stopPropagation` reaches the native event, so swallowing Escape here
    // would trap a keyboard user inside an open menu.
    expect(capturesCommandKey({ key: "Escape", ctrlKey: false })).toBe(false);
  });

  it("leaves ordinary typing alone", () => {
    for (const key of ["a", "/", "-", "Backspace", "ArrowLeft", "ArrowRight", " "]) {
      expect(capturesCommandKey({ key, ctrlKey: false }), key).toBe(false);
    }
  });
});

describe("the popover body says what THIS control is", () => {
  it("never falls through to the vendored default body", () => {
    // `ModelSelectorContent` renders its own search, list and effort row when
    // given no children — and that body is the one carrying every
    // model-specific string, including a hidden `aria-label="Model"` anchor
    // that put a combobox called Model inside five other controls.
    const text = code(CONTROL_PILL);

    expect(text).toContain("<ModelSelectorList>");
    expect(text).toContain("<ModelSelectorEmpty>");
    expect(text).not.toMatch(/<ModelSelectorContent[^>]*\/>/);
  });

  it("names its own entity in the search box, the empty state and the anchor", () => {
    const text = code(CONTROL_PILL);

    expect(text).toContain("placeholder={`Search ${noun}…`}");
    expect(text).toContain("aria-label={`Search ${noun}`}");
    expect(text).toContain("{`No ${noun} found.`}");
    // The unsearchable branch still anchors cmdk's keyboard navigation, and it
    // is named after the control rather than after models.
    expect(text).toContain("<ModelSelectorSearch readOnly aria-label={ariaLabel} />");
  });

  it("gives every pill a noun, so no list can inherit the vendored one", () => {
    // A census across FILES: each pill is individually plausible and the defect
    // is that one of them said nothing and got "models".
    const searchable = ["project-pill.tsx", "model-pill.tsx", "branch-pill.tsx"];
    for (const file of searchable) {
      expect(code(`${CONTROLS_DIR}/${file}`), file).toMatch(/searchNoun="[a-z ]+"/);
    }
    // `searchable` was a boolean beside nothing: it could turn a search box on
    // without saying what it searched, which is precisely how the placeholder
    // stayed vendored.
    for (const file of [...searchable, "agent-pill.tsx", "runtime-pill.tsx", "working-directory-pill.tsx"]) {
      expect(code(`${CONTROLS_DIR}/${file}`), file).not.toContain("searchable");
    }
  });
});

describe("the landing model rows", () => {
  it("uses canonical names and context windows while retaining ids as search keywords", () => {
    // The picker mounts its list only in an open portal, so this is a source
    // census of the row mapping. `modelOptions` owns all three display facts;
    // duplicating any one here would fork the vocabulary from the other pickers.
    const pill = code(`${CONTROLS_DIR}/model-pill.tsx`);
    const options = source("components/grove/model-option.tsx");

    expect(pill).toContain("modelOptions(catalog.data ?? [])");
    expect(pill).toContain("description, icon, keywords");
    expect(pill).toMatch(/id,\s+label: name,\s+description,\s+icon,\s+keywords,/);
    expect(options).toContain("name: declared ?? modelLabel(id, namespace)");
    expect(options).toContain("keywords: [id]");
    expect(options).toContain("...(window ? { description: window } : {})");
  });
});

describe("an option that reveals a field keeps its menu open", () => {
  it("closes only for options that ARE the answer", () => {
    // `ModelSelectorItem` closes on select unconditionally, which is right for
    // a model and wrong for `Custom…`: the field it reveals was mounted inside
    // a popover the same click had just dismissed, so the control looked inert
    // and the input was reachable only by reopening the menu.
    const text = code(CONTROL_PILL);

    expect(text).toContain("opensPanel === true");
    expect(text).toContain("if (!next && keepOpen.current)");
  });

  it("declares the modes that need a panel, and NOT the one that does not", () => {
    const branch = code(`${CONTROLS_DIR}/branch-pill.tsx`);
    const modes = branch.slice(branch.indexOf("BRANCH_MODES"), branch.indexOf("function branchNameError"));

    // `auto` is the whole answer — Grove names the branch — so selecting it
    // should dismiss the menu like any other complete choice.
    expect(modes.slice(modes.indexOf("auto:"), modes.indexOf("new:"))).not.toContain("opensPanel");
    for (const mode of ["new", "existing", "remote", "root"]) {
      const start = modes.indexOf(`${mode}: {`);
      expect(start, `${mode} is not a BRANCH_MODES entry`).toBeGreaterThan(-1);
      expect(modes.slice(start, start + 260), mode).toContain("opensPanel: true");
    }
  });

  it("marks the model pill's Custom option the same way", () => {
    expect(code(`${CONTROLS_DIR}/model-pill.tsx`)).toMatch(/label: "Custom…",[\s\S]{0,120}opensPanel: true/);
  });
});

describe("the custom model id lives in the picker, not on the row", () => {
  it("is a CHILD of the pill, so it renders inside the popover", () => {
    // On the row it was a text input competing with five controls for one line,
    // which is why it needed `basis-full` and a whole line of its own — width
    // the composer paid for as long as Custom stayed selected.
    const text = code(`${CONTROLS_DIR}/model-pill.tsx`);
    const open = text.indexOf("<LaunchPill");
    const close = text.indexOf("</LaunchPill>");

    expect(close).toBeGreaterThan(open);
    expect(text.slice(open, close)).toContain(`data-testid={LAUNCH_TESTIDS.customModel}`);
    expect(text).not.toContain("basis-full");
  });

  it("keeps the testids the e2e suite and the Send gate address it by", () => {
    const text = code(`${CONTROLS_DIR}/model-pill.tsx`);

    expect(text).toContain("LAUNCH_TESTIDS.customModel");
    expect(text).toContain("LAUNCH_TESTIDS.customModelError");
    // The error is still announced on the field itself, not only in the tooltip.
    expect(text).toContain("aria-describedby={validationError ? LAUNCH_TESTIDS.customModelError : undefined}");
  });

  it("still refuses an id the wire would reject", () => {
    // The validator is what disables Send; moving the field must not have moved
    // the rule with it.
    expect(code(`${CONTROLS_DIR}/model-pill.tsx`)).toContain("customModelError(values.model ?? \"\")");
  });
});

describe("an unavailable control does not strand an open menu", () => {
  it("drops the row's open key when its own pill becomes unusable", () => {
    // A project switch can take the model catalog away while its menu is open.
    // The popover then closes through the `open` expression and Radix returns
    // focus to a trigger that is now `disabled` — which cannot hold it — while
    // the row still believes this pill is the open one, so the menu springs
    // back the moment the control recovers.
    expect(code(CONTROL_PILL)).toContain("if (unavailable && openKind === kind) setOpenKind(null);");
  });
});
