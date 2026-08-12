import { TerminalIcon } from "lucide-react";

// Imported as the bare leaf, never the compound provider export (and never
// the package root): the compound object also attaches `.Avatar` and
// `.Combine`, and BOTH of those eagerly import `features/Icon{Avatar,Combine}`
// — which pulls in `@lobehub/ui`'s `@emoji-mart` dependency even though this
// file never renders either variant. That chain fails outright under
// Vitest's Node ESM loader (`@emoji-mart/data`'s JSON import needs an import
// attribute Node's loader doesn't grant it) and would otherwise ride along in
// the production bundle on tree-shaking alone. Verified on the `Color` leaves
// below the same way it was verified on `Mono`: each imports only `react`,
// its own `../style` (the `<title>` text) and, where its artwork needs a
// gradient, `../../hooks/useFillId` for a collision-safe `<linearGradient>`
// id — never `@lobehub/ui`. `Claude`'s and `Gemini`'s Color leaves render a
// bare `<svg>` with the brand fill(s) on their `<path>`s, same shape as
// `Mono`; Codex's Color leaf additionally draws its own opaque white
// rounded-square backing `<path>` behind the gradient mark (see below).
import Claude from "@lobehub/icons/es/Claude/components/Color";
import Codex from "@lobehub/icons/es/Codex/components/Color";
import Gemini from "@lobehub/icons/es/Gemini/components/Color";
// OpenAI ships no `Color` leaf at all in this pinned version — only `Mono`,
// `Avatar`, `Combine` and `Text` exist under `es/OpenAI/components/`; `style.js`
// exposes per-product hex constants (`colorGpt4`, `colorO1`, …) but nothing a
// component can render. There is no vendored colour artwork to switch to, so
// this one stays on `Mono` until lobehub ships one — not a stylistic choice.
import OpenAI from "@lobehub/icons/es/OpenAI/components/Mono";

import { agentBrand, type AgentBrand } from "@/components/grove/fleet/tokens";

/**
 * Marks by brand. Four of the five are lobehub's vendored brand icons — brand
 * artwork is trademarked, versioned and occasionally redrawn, so it arrives
 * from the registry like every other vendored asset rather than being traced
 * by hand here. `generic` is a lucide glyph because a plain shell has no brand
 * to draw, and drawing one would be a worse lie than a neutral terminal.
 *
 * `claude`, `codex` and `gemini` take lobehub's `Color` leaf: each carries a
 * fixed brand fill baked into its own path data, which is an IDENTITY hue
 * per §4.1 of the design system (a fixed property of the thing, not gated on
 * run state) — the same standing `--chart-1…5` already has, just paid in
 * vendored SVG fill instead of a token. That is what `lint:styling`'s
 * palette-utility ban does not reach: it forbids a *class* fixing a colour in
 * this tree, and a vendored `fill` attribute inside imported path data is
 * neither a class nor written here. `openai` stays on `Mono` (see the import
 * above) because no `Color` leaf exists for it in this pinned version.
 *
 * `claude` renders lobehub's generic `Claude` mark now, not the
 * tool-specific `ClaudeCode` one — reversed deliberately: people recognise
 * the Anthropic mark far more readily than a CLI-specific redraw of it, and
 * recognisability wins over naming the exact binary. `codex` keeps its own
 * vendored mark rather than borrowing `OpenAI`'s, since Codex and OpenAI stay
 * separate `AgentBrand`s (see `fleet/tokens.ts`) and the Codex Color leaf is
 * the one both this file and the fleet actually renders.
 */
const MARKS: Record<AgentBrand, React.ComponentType<React.SVGProps<SVGSVGElement>>> = {
  claude: Claude,
  codex: Codex,
  openai: OpenAI,
  gemini: Gemini,
  generic: TerminalIcon,
};

/**
 * The logo every fleet row leads with.
 *
 * Rendered as a bare `<svg>` rather than wrapped, so it stays the DIRECT child
 * a `Button`'s `has-[>svg]` padding rule looks for. It is decorative: the
 * agent's name is already in the row's tooltip, and announcing it again would
 * make a screen reader read every row twice.
 */
export function AgentMark({
  agentName,
  className = "size-4",
}: {
  agentName: string;
  className?: string;
}): React.ReactNode {
  const brand = agentBrand(agentName);
  const Mark = MARKS[brand];
  return <Mark aria-hidden className={className} data-testid="agent-mark" data-brand={brand} />;
}
