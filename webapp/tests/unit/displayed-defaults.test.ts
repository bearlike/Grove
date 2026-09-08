import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

/**
 * A CONTROL MAY ONLY DISPLAY A VALUE SOMETHING ACTUALLY RESOLVED.
 *
 * Both fixtures below are the same defect wearing different clothes: a surface
 * that invents an answer, and a surface that asserts an equality, in each case
 * about a value only the daemon can supply. Neither throws, neither fails to
 * compile, and both look completely normal on screen — which is why they are
 * pinned at the source level, in the style of `launch-control-row.test.ts`:
 * these are JSX inside components that need a query client, a popover and a
 * live workspace to render, so there is no artifact a `node`-environment suite
 * can inspect instead.
 */
const RUNTIME_PILL = "components/grove/launch/controls/runtime-pill.tsx";
const MODEL_PILL = "components/grove/launch/controls/model-pill.tsx";
const COMPOSER_MODEL = "components/grove/workspace/composer-model.tsx";

function source(path: string): string {
  const text = readFileSync(path, "utf8");
  // The absence-check rule: a census that reports "clean" after reading an
  // empty file is indistinguishable from one that read the real thing.
  expect(text.length, `${path} is empty`).toBeGreaterThan(500);
  return text;
}

/** Strip comments, so prose EXPLAINING a rejected pattern cannot fail the scan. */
function code(path: string): string {
  return source(path)
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "");
}

describe("the launch runtime pill", () => {
  it("never falls back to a runtime literal the cascade did not name", () => {
    // `container.enabled` defaults to TRUE, so a `?? "host"` fallback rendered
    // between first paint and the `/defaults` response claimed Host on every
    // project — and the request omits an untouched field, so the engine then
    // resolved a container. Submitting is one keypress on this surface, so
    // that window produced real workspaces contradicting their own pill.
    const text = code(RUNTIME_PILL);

    expect(text).toContain("defaults.data?.runtime ?? null");
    expect(text).not.toMatch(/\?\?\s*"(host|container)"/);
  });

  it("still resolves a user choice ahead of the cascade's answer", () => {
    // The scan above is only meaningful while the precedence it guards exists.
    expect(code(RUNTIME_PILL)).toContain("values.runtime ?? defaults.data?.runtime");
  });
});

describe("the launch model pill", () => {
  it("never falls back to a catalog entry nobody chose", () => {
    // The runtime pill's `?? "host"` one file over, wearing a different literal:
    // `?? catalog[0]` named `anthropic-deepseek-v4-pro` merely because it sorts
    // first in one gateway's configured list. With no saved model the request
    // omits the field and the engine appends NO `--model` at all
    // (`test_with_no_saved_model_the_agent_is_launched_with_no_model_flag_at_all`
    // pins that), so the pill was promising a model that would not be used.
    const text = code(MODEL_PILL);

    expect(text).toContain("defaults.data?.model ?? null");
    expect(text).not.toMatch(/\?\?\s*catalog\[0\]/);
  });

  it("names the omitted state as a value rather than as a placeholder", () => {
    expect(code(MODEL_PILL)).toContain('"Agent default"');
  });
});

describe("the workspace composer model menu", () => {
  it("keeps the reported model out of selection state", () => {
    // `models` is the agent's `--model` catalog while `current_model` is the
    // provider report. They are different namespaces, so selection belongs to
    // the request this control delivered, never a comparison to the report.
    const text = code(COMPOSER_MODEL);

    expect(text).not.toMatch(/===\s*data\.current_model/);
    expect(text).toContain("requested?.id ?? currentModel ?? \"Agent default\"");
    expect(text).toContain("currentModel !== requested.reportedModel");
  });

  it("composes the vendored searchable model selector", () => {
    const text = code(COMPOSER_MODEL);

    expect(text).toContain("<ModelSelector.Search />");
    // Rows come from the ONE catalog reader every picker shares; the full id
    // stays searchable there (`keywords: [id]`) after the namespace is folded.
    expect(text).toContain("modelOptions(");
    // MEMBERSHIP stays the live session's own switch vocabulary: the catalog
    // supplies names and windows per row and must never decide which rows
    // exist, or the menu offers a model the agent cannot switch to. Pinned as
    // the join rather than as one spelling of it.
    expect(text).toContain("enrichedCatalog(ids,");
    // The row sizes ITSELF. It was pinned to 26px when a row was one line;
    // it now carries a name over its context window, and a fixed height
    // crushed the two together. Spacing lives at the theme boundary so all
    // three pickers agree — see `model-selector-group` in globals.css.
    expect(text).not.toContain("h-[26px]");
    expect(text).toContain("<ModelSelector.Group heading={namespace || undefined}>");
    expect(text).toContain("data-testid=\"composer-model-pending\"");
  });

  it("reports delivery through Sonner rather than the composer shell", () => {
    const text = code(COMPOSER_MODEL);

    expect(text).toContain('toast.success("Model request delivered"');
    expect(text).toContain('toast.error("Couldn’t deliver model request"');
    expect(text).not.toContain('role="status"');
    expect(text).not.toContain("bottom-full");
  });
});
