import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AppIcon, isIconSlug } from "@/components/grove/app-icon";
import { enrichedCatalog, modelOptions } from "@/components/grove/model-option";
import {
  contextWindowLabel,
  modelIconSlug,
  modelLabel,
  modelNamespace,
} from "@/lib/grove/adapters/model";

// A real gateway catalog AS THE ENGINE OFFERS IT, and a real Codex one. Both
// shapes matter: the fold must fire on the first and must NOT fire on the
// second. Note no `x` / `x[1m]` pair appears — `resolve_models` folds the
// redundant half out before any client sees the catalog, so a fixture carrying
// both halves would describe a list nothing produces.
const GATEWAY = [
  "anthropic-deepseek-v4-pro[1m]",
  "anthropic-fable-5-1",
  "anthropic-gpt-6-astra",
  "anthropic-opus-5",
  "anthropic-qwen3.7-max",
];
const CODEX = ["gpt-6-astra", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.5"];

describe("modelNamespace", () => {
  it("folds a vendor prefix every id shares", () => {
    expect(modelNamespace(GATEWAY)).toBe("anthropic-");
  });

  it("refuses a fold that would leave a remainder starting with a digit", () => {
    // `gpt-` is shared, but `6-astra` is not a model anyone would recognise.
    expect(modelNamespace(CODEX)).toBe("");
  });

  it("never folds half a word, and never a whole id", () => {
    expect(modelNamespace(["fable", "fable-5"])).toBe("");
    expect(modelNamespace(["opus", "opus-5", "opus-4"])).toBe("");
    expect(modelNamespace(["only-one"])).toBe("");
  });

  it("folds on slash and colon separators too", () => {
    expect(modelNamespace(["openai/gpt-6", "openai/o4-mini"])).toBe("openai/");
  });
});

describe("modelLabel", () => {
  it("strips the namespace and leaves versioned ids verbatim", () => {
    expect(modelLabel("anthropic-opus-5", "anthropic-")).toBe("opus-5");
    expect(modelLabel("gpt-6-astra")).toBe("gpt-6-astra");
    // The `[1m]` marker is the ONE thing besides the namespace that a label
    // drops — it is wire syntax rather than part of the name, and the row
    // states the window in words underneath. See the marker block below.
    expect(modelLabel("anthropic-opus-5[1m]", "anthropic-")).toBe("opus-5");
  });

  it("capitalises only a bare all-letter alias", () => {
    expect(modelLabel("fable")).toBe("Fable");
    expect(modelLabel("my-model name")).toBe("My-Model Name");
    expect(modelLabel("claude-opus-4-20250514")).toBe("claude-opus-4-20250514");
  });

  it("ignores a namespace the id does not carry", () => {
    expect(modelLabel("gpt-6-astra", "anthropic-")).toBe("gpt-6-astra");
  });
});

describe("modelIconSlug", () => {
  it("resolves the family word inside a gateway id, product before vendor", () => {
    expect(modelIconSlug("anthropic-deepseek-v4-pro")).toBe("logos:deepseek-icon");
    expect(modelIconSlug("anthropic-gpt-6-astra")).toBe("simple-icons:openai");
    expect(modelIconSlug("anthropic-glm-5.2")).toBe("thesvg-color:zhipu");
    expect(modelIconSlug("anthropic-qwen3.7-max[1m]")).toBe("logos:qwen-icon");
    expect(modelIconSlug("anthropic-opus-5")).toBe("logos:claude-icon");
    expect(modelIconSlug("fable")).toBe("logos:claude-icon");
  });

  it("returns null for an id no family matches", () => {
    expect(modelIconSlug("acme-sonnet")).toBe("logos:claude-icon");
    expect(modelIconSlug("acme-widget-9")).toBeNull();
  });
});

describe("AppIcon", () => {
  it("accepts only a prefix:name slug", () => {
    expect(isIconSlug("logos:claude-icon")).toBe(true);
    expect(isIconSlug("thesvg-color:zhipu")).toBe(true);
    expect(isIconSlug("claude")).toBe(false);
    expect(isIconSlug("logos:claude icon")).toBe(false);
    expect(isIconSlug("../logos:claude")).toBe(false);
    expect(isIconSlug(null)).toBe(false);
  });

  it("renders the key fallback for an invalid slug, and while the icon module loads", () => {
    const invalid = renderToStaticMarkup(<AppIcon slug="not a slug" />);
    expect(invalid).toContain('data-slot="app-icon-fallback"');
    expect(invalid).not.toContain('data-slot="app-icon"');
    // SSR never resolves the lazy module, so a valid slug renders the SAME
    // fallback at the same size: that is what keeps a row from shifting.
    const pending = renderToStaticMarkup(<AppIcon slug="logos:claude-icon" className="size-3.5" />);
    expect(pending).toContain('data-slot="app-icon-fallback"');
    expect(pending).toContain("size-3.5");
  });
});

describe("modelOptions", () => {
  it("returns the namespace once and label/keywords per id", () => {
    const { namespace, options } = modelOptions(GATEWAY);
    expect(namespace).toBe("anthropic-");
    expect(options.map((o) => o.id)).toEqual(GATEWAY);
    // The marked id reads as the plain model — the marker is wire syntax and
    // the row's own second line states the window.
    expect(options.map((o) => o.name)).toEqual([
      "deepseek-v4-pro",
      "fable-5-1",
      "gpt-6-astra",
      "opus-5",
      "qwen3.7-max",
    ]);
    // The folded-out namespace AND the marker must both stay searchable.
    expect(options[0]?.keywords).toEqual(["anthropic-deepseek-v4-pro[1m]"]);
    expect(renderToStaticMarkup(<>{options[0]?.icon}</>)).toContain('data-testid="model-mark"');
  });

  it("leaves a Codex catalog whole", () => {
    const { namespace, options } = modelOptions(CODEX);
    expect(namespace).toBe("");
    expect(options.map((o) => o.name)).toEqual(CODEX);
  });

  it("prefers a declared name and still keeps the raw id searchable", () => {
    const { options } = modelOptions([
      { id: "anthropic-gpt-5.6-luna", name: "GPT-5.6 Luna", context_window: 353_400 },
      { id: "anthropic-opus-5", name: null, context_window: 1_000_000 },
    ]);
    // Declared wins; undeclared falls back to the id's own folded spelling —
    // never a guessed name.
    expect(options.map((o) => o.name)).toEqual(["GPT-5.6 Luna", "opus-5"]);
    // A reader who knows the provider's spelling must still find the renamed row.
    expect(options[0]?.keywords).toEqual(["anthropic-gpt-5.6-luna"]);
  });

  it("describes a published window and says NOTHING about an absent one", () => {
    const { options } = modelOptions([
      { id: "anthropic-opus-5", name: null, context_window: 1_000_000 },
      { id: "anthropic-glm-5.3", name: null, context_window: null },
    ]);
    expect(options[0]?.description).toBe("1M context window");
    // The central honesty rule: an unmeasured window is an ABSENT line, never
    // a row claiming zero context.
    expect(options[1]?.description).toBeUndefined();
  });

  it("takes bare ids too, because a live session's vocabulary is strings", () => {
    const { options } = modelOptions(["opus", "sonnet"]);
    expect(options.map((o) => o.name)).toEqual(["Opus", "Sonnet"]);
    expect(options.every((o) => o.description === undefined)).toBe(true);
  });

  it("never offers an effort control", () => {
    const { options } = modelOptions([
      { id: "anthropic-opus-5", name: "Opus 5", context_window: 1_000_000 },
    ]);
    expect(options.every((o) => o.efforts === undefined)).toBe(true);
  });
});

describe("enrichedCatalog", () => {
  const CATALOG = [
    { id: "anthropic-opus-5", name: "Opus 5", context_window: 1_000_000 },
    { id: "anthropic-sonnet-5", name: "Sonnet 5", context_window: 1_000_000 },
  ];

  it("keeps MEMBERSHIP with the ids and takes only display facts from the catalog", () => {
    // The live session can switch to `anthropic-opus-5` and to a model the
    // catalog has never heard of; it cannot switch to `anthropic-sonnet-5`.
    const entries = enrichedCatalog(["anthropic-opus-5", "anthropic-glm-5.3"], CATALOG);
    expect(entries.map((e) => (typeof e === "string" ? e : e.id))).toEqual([
      "anthropic-opus-5",
      "anthropic-glm-5.3",
    ]);
    const { options } = modelOptions(entries);
    expect(options[0]?.name).toBe("Opus 5");
    expect(options[0]?.description).toBe("1M context window");
    // An id the catalog never named keeps its own spelling (the namespace fold
    // is the only thing applied to it) and claims no window.
    expect(options[1]?.name).toBe("glm-5.3");
    expect(options[1]?.description).toBeUndefined();
  });

  it("passes the ids straight through when no catalog has loaded", () => {
    expect(enrichedCatalog(["opus", "sonnet"], undefined)).toEqual(["opus", "sonnet"]);
    expect(enrichedCatalog(["opus"], [])).toEqual(["opus"]);
  });
});

describe("the long-context marker", () => {
  it("is spelled identically in the engine and the client", () => {
    // Two literals, one meaning: `registry.py` decides WHICH id of a redundant
    // pair is offered, `adapters/model.ts` decides how that id READS. They are
    // too short to be worth a wire field and too coupled to drift silently —
    // if the engine started emitting `[1M]`, the label would stop stripping it
    // and every row would print the marker again with nothing failing.
    const engine = readFileSync(
      resolve(__dirname, "../../../src/grove/core/agents/registry.py"),
      "utf8",
    );
    const match = engine.match(/^CONTEXT_VARIANT_SUFFIX = "(.+)"$/m);
    expect(match?.[1], "registry.py must declare CONTEXT_VARIANT_SUFFIX").toBeDefined();

    const client = readFileSync(resolve(__dirname, "../../lib/grove/adapters/model.ts"), "utf8");
    expect(client).toContain(`const CONTEXT_VARIANT_SUFFIX = "${match?.[1]}";`);
  });

  it("is stripped from a label but never from the id", () => {
    // The row already states the window in words underneath, so printing the
    // marker is the same fact twice in a spelling nobody says out loud.
    const { options } = modelOptions([
      { id: "anthropic-opus-5[1m]", name: null, context_window: 1_000_000 },
      { id: "anthropic-glm-5.3", name: null, context_window: 892_928 },
    ]);
    expect(options[0]?.name).toBe("opus-5");
    // The id is what gets SENT and what a reader may search for, so it is
    // untouched on both counts.
    expect(options[0]?.id).toBe("anthropic-opus-5[1m]");
    expect(options[0]?.keywords).toEqual(["anthropic-opus-5[1m]"]);
  });

  it("does not disturb the namespace fold it now sits beside", () => {
    // Every remainder still starts with a letter, so the fold still fires —
    // the marker is a suffix and the guard reads the head.
    expect(
      modelNamespace(["anthropic-opus-5[1m]", "anthropic-glm-5.3", "anthropic-sonnet-5[1m]"]),
    ).toBe("anthropic-");
  });

  it("leaves a declared name alone, marker or not", () => {
    // A declared name is what the operator typed; stripping anything from it
    // would be the client editing their answer.
    const { options } = modelOptions([
      { id: "anthropic-opus-5[1m]", name: "Claude Opus 5", context_window: 1_000_000 },
    ]);
    expect(options[0]?.name).toBe("Claude Opus 5");
  });
});

describe("contextWindowLabel", () => {
  it("reads as scale, not precision", () => {
    expect(contextWindowLabel(1_000_000)).toBe("1M context window");
    expect(contextWindowLabel(1_100_000)).toBe("1.1M context window");
    expect(contextWindowLabel(1_048_576)).toBe("1M context window");
    expect(contextWindowLabel(353_400)).toBe("353.4K context window");
    expect(contextWindowLabel(131_072)).toBe("131.1K context window");
  });

  it("is null for every shape that is not a measurement", () => {
    // Each of these would otherwise render as a confident claim about a model
    // nobody measured.
    expect(contextWindowLabel(null)).toBeNull();
    expect(contextWindowLabel(undefined)).toBeNull();
    expect(contextWindowLabel(0)).toBeNull();
    expect(contextWindowLabel(-1)).toBeNull();
    expect(contextWindowLabel(Number.NaN)).toBeNull();
    expect(contextWindowLabel(Number.POSITIVE_INFINITY)).toBeNull();
  });
});
