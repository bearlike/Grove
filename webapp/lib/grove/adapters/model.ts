/**
 * How a model id READS: its namespace, its display label and its brand mark.
 *
 * Every picker in the app — the landing pill, the workspace composer, the
 * create dialog — asks these three questions of the same catalog, and each
 * used to answer them differently. Ids stay opaque on the wire: nothing here
 * ever changes what is sent to the provider, only what a reader sees.
 */

/**
 * The long-context marker a gateway appends to a second id for one model.
 *
 * Mirrors `CONTEXT_VARIANT_SUFFIX` in `core/agents/registry.py`, which folds
 * the redundant half of a pair out of the catalog. Two spellings of a literal
 * this short is cheaper than a wire field for it, but they must move together:
 * the engine decides WHICH id is offered, this decides how that id reads.
 */
const CONTEXT_VARIANT_SUFFIX = "[1m]";

/**
 * The namespace a whole catalog shares, or `""` when there is none.
 *
 * A gateway publishes `anthropic-opus-5`, `anthropic-sonnet-5`, … and the
 * repeated word is noise the group heading can carry once. The fold is only
 * legitimate when it stops at a separator AND every remainder still starts
 * with a letter: Codex's `gpt-6-astra` / `gpt-5.6-sol` share `gpt-` too, but
 * `6-astra` is not a model name anyone would recognise, so that catalog keeps
 * its ids whole.
 */
export function modelNamespace(ids: readonly string[]): string {
  if (ids.length < 2) return "";
  let prefix = ids[0] ?? "";
  for (const id of ids) {
    while (prefix && !id.startsWith(prefix)) prefix = prefix.slice(0, -1);
  }
  const boundary = prefix.search(/[-/:][^-/:]*$/);
  if (boundary < 0) return "";
  const namespace = prefix.slice(0, boundary + 1);
  return ids.every((id) => /^[a-z]/i.test(id.slice(namespace.length))) ? namespace : "";
}

/**
 * What a picker prints for one id: the remainder after the catalog namespace,
 * with a bare all-letter alias (`opus`, `sonnet`) capitalised. Versioned or
 * dotted ids are left exactly as the provider spells them — a label that
 * differs from the id by more than case is a second name to learn.
 *
 * The one exception is the trailing `[1m]` long-context marker, which is WIRE
 * SYNTAX rather than part of the model's name: the engine offers only the
 * marked id of a redundant pair, and the row already states the window in
 * words underneath, so printing it would be the same fact twice in a spelling
 * nobody says out loud. The id itself is untouched and stays searchable
 * through `keywords`.
 */
export function modelLabel(id: string, namespace = ""): string {
  const withoutNamespace = namespace && id.startsWith(namespace) ? id.slice(namespace.length) : id;
  const shown = withoutNamespace.endsWith(CONTEXT_VARIANT_SUFFIX)
    ? withoutNamespace.slice(0, -CONTEXT_VARIANT_SUFFIX.length)
    : withoutNamespace;
  if (!/^[a-z]+(?:[- ][a-z]+)*$/.test(shown)) return shown;
  return shown.replace(/(^|[- ])([a-z])/g, (_, sep: string, ch: string) => `${sep}${ch.toUpperCase()}`);
}

/**
 * How a context window READS on a row: `1.1M` / `131K` tokens.
 *
 * Three significant figures below a full unit, none above it, so `1000000`
 * is `1M` rather than `1.0M` and `353400` is `353K` rather than `353.4K` —
 * the number is a sense of scale, and a decimal place nobody compares across
 * rows is noise in a 12px line.
 *
 * `null` in, `null` out: a model no source published a window for says nothing,
 * because a row reading `0 tokens` about a model that holds a million is the
 * one claim this whole path exists to prevent.
 */
export function contextWindowLabel(tokens: number | null | undefined): string | null {
  if (tokens == null || !Number.isFinite(tokens) || tokens <= 0) return null;
  for (const [limit, suffix] of [
    [1_000_000, "M"],
    [1_000, "K"],
  ] as const) {
    if (tokens >= limit) {
      const scaled = tokens / limit;
      // `toFixed(1)` then strip a trailing `.0`: 1048576 → `1M`, 1100000 → `1.1M`.
      return `${scaled.toFixed(1).replace(/\.0$/, "")}${suffix} context window`;
    }
  }
  return `${tokens} context window`;
}

/**
 * Brand marks keyed by the family word inside an id. Iconify slugs, verified
 * live against `api.iconify.design` when added; the first family that matches
 * wins, so the more specific product names sit above their vendor. A mark
 * whose artwork is a single black fill takes the `currentColor` set
 * (`simple-icons`) rather than `logos`, or it vanishes on the dark ladder.
 */
const MODEL_MARKS: readonly (readonly [RegExp, string])[] = [
  [/claude|opus|sonnet|haiku|fable|mythos/i, "logos:claude-icon"],
  [/gpt|codex|openai|o[1-9]-/i, "simple-icons:openai"],
  [/gemini|gemma/i, "logos:google-gemini-icon"],
  [/deepseek/i, "logos:deepseek-icon"],
  [/qwen/i, "logos:qwen-icon"],
  [/glm|zhipu/i, "thesvg-color:zhipu"],
  [/kimi|moonshot/i, "simple-icons:kimi"],
  [/mistral|mixtral/i, "logos:mistral-ai-icon"],
  [/grok/i, "logos:grok"],
];

/** The icon slug for a model id, or `null` when no family is recognised. */
export function modelIconSlug(id: string): string | null {
  return MODEL_MARKS.find(([pattern]) => pattern.test(id))?.[1] ?? null;
}
