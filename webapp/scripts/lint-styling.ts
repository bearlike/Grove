/**
 * Fail if `components/grove/**` styles instead of composes.
 *
 * WHERE THE LINE IS. Grove code may lay things out; it may not invent a look.
 *   allowed   layout and spacing — flex, grid, gap, size-*, p-*, m-*, w-*, h-*
 *   allowed   the theme's SEMANTIC tokens — `bg-sidebar-primary`, `text-muted-
 *             foreground`, `border-border`. These are vendored vocabulary: the
 *             theme owns what they resolve to, so using one is composition, and
 *             a dark-mode fix upstream reaches us for free.
 *   forbidden Tailwind's raw palette (`bg-blue-500`, `text-zinc-100`), `white`
 *             /`black`, colour literals in arbitrary values, and every radius
 *             or shadow utility — those hard-code a look that then drifts from
 *             the vendored components around it.
 *
 * The palette is distinguishable from a token by shape alone: a palette class
 * ends in a 2-3 digit scale step. `chart-1`…`chart-5` are single-digit tokens
 * and stay allowed, which is why the step is 2-3 digits and not `\d+`.
 */

import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";

const ROOT = "components/grove";

/**
 * Files that are PORTS of assistant-ui's own published example code, and so
 * legitimately carry its styling verbatim.
 *
 * WHY this hole exists at all: the registry's `Thread` sets
 * `--thread-max-width` inline on its root and hard-codes `UserMessage`, so
 * width, user-message collapse and a composer-adjacent plan card are all
 * unreachable through its `components` prop. assistant-ui's own `base.tsx` is
 * app code composing `ThreadPrimitive`/`MessagePrimitive` directly — so
 * composing the primitives ourselves is their sanctioned path, not a bespoke
 * component. Reproducing their look then means reproducing their classes.
 *
 * WHY it is a list and not a marker comment: a `// @ported` comment anyone can
 * type would decay into a way to silence the linter. Adding a path here is a
 * deliberate, reviewable act, and the URL is the claim being made — that this
 * file tracks that upstream file and can be diffed against it by hand.
 */
const PORTED_FILES: Record<string, string> = {
  "components/grove/workspace/thread.tsx":
    "https://github.com/assistant-ui/assistant-ui/blob/main/apps/docs/components/examples/base.tsx",
  // Mirrors the vendored heat-graph anatomy exactly — month labels, day labels,
  // cell grid, legend, tooltip — and differs in one input: `colorScale` reads
  // the `--heat-*` ramp instead of the hard-coded blue const. The component
  // takes only `data`, so there is no prop to pass the scale through.
  "components/grove/usage/activity-heatmap.tsx": "components/assistant-ui/heat-graph.tsx",
  "components/grove/workspace/pending-question.tsx": "components/elements/elicitation-form.tsx",
};

/** Utilities whose value is a colour. */
const COLOUR_UTILITIES =
  "bg|text|border|ring|fill|stroke|from|via|to|outline|decoration|divide|accent|caret|placeholder|shadow";

const RULES: { pattern: RegExp; reason: string }[] = [
  {
    pattern: new RegExp(String.raw`\b(?:${COLOUR_UTILITIES})-[a-z]+-\d{2,3}\b`, "g"),
    reason: "Tailwind palette colour",
  },
  {
    pattern: new RegExp(String.raw`\b(?:${COLOUR_UTILITIES})-(?:white|black)\b`, "g"),
    reason: "hard-coded white/black",
  },
  {
    pattern: /\[(?:#[0-9a-fA-F]{3,8}|(?:rgb|rgba|hsl|hsla|oklch|color-mix)\([^\]]*\))\]/g,
    reason: "colour literal in an arbitrary value",
  },
  { pattern: /\[--[\w-]+:/g, reason: "bespoke CSS variable" },
  { pattern: /\brounded(?:-[a-z]+)*(?:-(?:none|xs|sm|md|lg|xl|\d?xl|full))?\b/g, reason: "radius utility" },
  { pattern: /\bshadow(?:-[a-z0-9/]+)?\b/g, reason: "shadow utility" },
];

/**
 * Blank out comment bodies, preserving every line break and column.
 *
 * WHY: the developer most likely to write the word "rounded" is the one
 * explaining why they did NOT reach for a radius utility — so scanning raw text
 * flags precisely the person complying with the rule. Positions are preserved
 * rather than the comments deleted, so reported line numbers stay true.
 *
 * `//` is only a comment when it is not a URL's `://`. That heuristic is
 * cheaper than a parser and wrong only for a `//` inside a string literal,
 * which would at worst hide a violation rather than invent one.
 */
function stripComments(source: string): string {
  const blank = (text: string): string => text.replace(/[^\n]/g, " ");
  return source
    .replace(/\/\*[\s\S]*?\*\//g, blank)
    .replace(/(^|[^:])\/\/[^\n]*/g, (match, lead: string) => lead + blank(match.slice(lead.length)));
}

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walk(path));
    else if (/\.tsx?$/.test(entry.name)) out.push(path);
  }
  return out;
}

function main(): void {
  const violations: string[] = [];
  const ported: string[] = [];

  for (const file of walk(ROOT)) {
    const key = relative(".", file).split(/[\\/]/).join("/");
    const upstream = PORTED_FILES[key];
    if (upstream !== undefined) {
      ported.push(`${key}  ← ${upstream}`);
      continue;
    }
    const lines = stripComments(readFileSync(file, "utf8")).split("\n");
    lines.forEach((line, i) => {
      for (const { pattern, reason } of RULES) {
        for (const match of line.matchAll(pattern)) {
          violations.push(`${relative(".", file)}:${i + 1}  ${match[0]}   — ${reason}`);
        }
      }
    });
  }

  // Always name the exemptions, pass or fail. An allowlist nobody sees is an
  // allowlist that quietly grows.
  for (const entry of ported) console.log(`  ported (not scanned): ${entry}`);

  if (violations.length === 0) {
    console.log(`✓ lint:styling — components/grove composes without styling.`);
    return;
  }

  console.error(`\n✗ lint:styling — ${violations.length} styling utility/utilities in components/grove:\n`);
  for (const v of violations) console.error(`    ${v}`);
  console.error(
    "\n  components/grove composes vendored components; it does not restyle them.\n" +
      "  Instead:\n" +
      "    • colour   → use a semantic theme token (bg-muted, text-muted-foreground,\n" +
      "                 border-border), or a vendored component's own variant prop.\n" +
      "    • radius   → let the vendored component supply it (Card, Badge, Button …);\n" +
      "                 wrap rather than round.\n" +
      "    • shadow   → the same. If no vendored component fits, add one with\n" +
      "                 `npx shadcn@latest add` / `npx assistant-ui@latest add`.\n",
  );
  process.exit(1);
}

main();
