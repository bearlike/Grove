import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

/**
 * The create dialog's model picker is a source census because its dialog content
 * mounts in a portal and the node renderer has no open-dialog artifact to read.
 */
const DIALOG = "components/grove/fleet/create-workspace-dialog.tsx";

/** Strip comments so prose cannot satisfy a composition or absence check. */
function code(path: string): string {
  const text = readFileSync(path, "utf8");
  expect(text.length, `${path} is empty`).toBeGreaterThan(500);
  return text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

function modelField(text: string): string {
  const start = text.indexOf('<Field label="Model">');
  const end = text.indexOf('<Field label="Runtime">');

  expect(start).toBeGreaterThan(-1);
  expect(end).toBeGreaterThan(start);
  return text.slice(start, end);
}

describe("the create dialog's model picker", () => {
  it("composes the vendored searchable ModelSelector rather than a Select", () => {
    const field = modelField(code(DIALOG));

    for (const part of [
      "ModelSelector.Root",
      "ModelSelector.Trigger",
      "ModelSelector.Content",
      "ModelSelector.Search",
      "ModelSelector.List",
      "ModelSelector.Empty",
      "ModelSelector.Group",
      "ModelSelector.Item",
    ]) {
      expect(field).toContain(part);
    }
    expect(field).not.toContain("<Select");
  });

  it("uses the outlined trigger that keeps Agent default above catalog choices", () => {
    const field = modelField(code(DIALOG));

    expect(field).toMatch(/<ModelSelector\.Trigger\s+variant="outline"/);
    expect(field).toContain("heading={modelNamespace || undefined}");
    expect(field.indexOf("Agent default")).toBeLessThan(field.indexOf("<ModelSelector.Group"));
  });

  it("reads the per-agent catalog and keeps each full id reachable", () => {
    const text = code(DIALOG);

    expect(text).toContain("useModels(repoRoot || null, agentName || null)");
    expect(text).not.toContain("agent?.models");
    expect(text).toContain("modelOptions(models.data ?? [])");
    expect(text).toContain("title={option.id}");
  });

  it("does not enable reasoning effort selection", () => {
    expect(code(DIALOG)).not.toMatch(/\befforts\s*=/);
  });

  it("gives the selector the sentinel ROW, not just the catalog", () => {
    // The bare Trigger resolves its label by looking `value` up in `models`.
    // With a catalog-only list the default selection renders the vendor's
    // "Select model" placeholder — a control saying nothing is chosen while the
    // request it builds carries the resolved default. Verified by rendering the
    // vendored trigger directly (below); pinned here as the composition that
    // makes it true, since this dialog's content mounts in a portal.
    const field = modelField(code(DIALOG));
    expect(field).toContain("models={selectableRows}");
    expect(code(DIALOG)).toContain("[AGENT_DEFAULT_ROW, ...modelRows]");
  });
});

describe("the vendored trigger's label resolution", () => {
  // The behavioural half of the rule above, and the reason it is a separate
  // block: this needs no dialog, so a static render CAN see it.
  it("prints a placeholder for a value absent from `models`", async () => {
    const { renderToStaticMarkup } = await import("react-dom/server");
    const { ModelSelector } = await import("@/components/assistant-ui/model-selector");

    const absent = renderToStaticMarkup(
      <ModelSelector.Root models={[{ id: "anthropic-opus-5", name: "Opus 5" }]} value="inherit">
        <ModelSelector.Trigger variant="outline" />
      </ModelSelector.Root>,
    );
    expect(absent).toContain("Select model");

    const present = renderToStaticMarkup(
      <ModelSelector.Root
        models={[
          { id: "inherit", name: "Agent default" },
          { id: "anthropic-opus-5", name: "Opus 5" },
        ]}
        value="inherit"
      >
        <ModelSelector.Trigger variant="outline" />
      </ModelSelector.Root>,
    );
    expect(present).toContain("Agent default");
    expect(present).not.toContain("Select model");
  });
});
