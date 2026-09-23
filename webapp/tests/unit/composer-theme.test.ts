import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

/**
 * The native composer's theme seam, pinned by SCOPE and TOKEN.
 *
 * `components/elements/composer.tsx` is vendored and writes its type in pixel
 * literals, which is the one thing the density root cannot reach — so the ramp
 * has to arrive from `globals.css`. There is no DOM and no stylesheet engine
 * here, and nothing in the markup a `renderToStaticMarkup` could show would
 * regress: what regresses is whether the theme still reaches upstream's element
 * and still says it in a token.
 *
 * Each assertion is therefore one of exactly two claims:
 *
 *   SCOPE   confined to the composer. A blanket rule on a shared slot
 *           (`textarea`, `badge`) silently restyles every form in the app and is
 *           invisible anywhere a composer is not on screen.
 *   TOKEN   the value is a ramp step or a ladder rung, not a literal. A pixel
 *           opts the composer out of the density root, the reader's own font
 *           size and browser zoom at once (design-system §1).
 *
 * Deliberately NOT pinned: the exact declaration strings. A shelf that grows a
 * second background layer, or a chip whose metadata changes tier, are visual
 * decisions the design system owns — a test spelling them would fail on every
 * such decision while catching neither failure above.
 */

/**
 * Comments stripped ONCE, with every read below going through the result.
 *
 * The person most likely to write a slot name in prose is the one explaining the
 * scope rule, so scanning raw text flags exactly the compliant author —
 * `lint-styling.ts` strips for the same reason.
 */
const CSS = readFileSync(new URL("../../app/globals.css", import.meta.url), "utf8").replace(
  /\/\*[\s\S]*?\*\//g,
  "",
);

/**
 * Every rule as one selector paired with its own body.
 *
 * A comma-separated group is split, because both halves of this suite ask
 * questions about a single selector: "is THIS one scoped" and "does THIS one
 * pair its step with its leading". Carrying the body alongside is load-bearing
 * rather than tidy — a helper that looked a body up by substring returned the
 * wrong rule outright, since one group's selector list is a prefix of a longer
 * group's, and a wrong body reads exactly like an absent rule.
 */
const RULES: readonly { selector: string; body: string }[] = [
  ...CSS.matchAll(/([^{}]+)\{([^{}]*)\}/g),
].flatMap((match) =>
  match[1]!
    .split(",")
    .map((selector) => selector.trim())
    .filter((selector) => selector.length > 0)
    .map((selector) => ({ selector, body: match[2]! })),
);

/** Every rule the composer seam owns: its own classes, plus any composer slot. */
const COMPOSER = RULES.filter(
  ({ selector }) => selector.includes("composer") || selector.includes("launch-brand-field"),
);

/**
 * The three text entries the app actually mounts, and how each is addressable.
 *
 * This is the census the seam has to satisfy, and it is not one selector:
 * `ComposerInput` is the vendored single-line input and exports a slot;
 * `ComposerPrimitive.Input` is what the workspace composer mounts and exports
 * only `.aui-composer-input`, NO slot; the shared composer substitutes the
 * vendored `Textarea`. A slot-only seam reaches two of the three and misses the
 * one on the busiest surface in the app — which looks identical to working,
 * because the other two are correct.
 */
const TEXT_ENTRIES: readonly string[] = [
  '[data-slot="composer-input"]',
  ".aui-composer-input",
  '[data-slot="textarea"]',
];

/** The body of the one rule declaring a class, or null where it is absent. */
function classBody(name: string): string | null {
  return RULES.find(({ selector }) => selector === name)?.body ?? null;
}

describe("the composer theme seam is SCOPED to the composer", () => {
  it("never restyles a shared slot without a composer ancestor", () => {
    // `textarea` is the case that motivated this: the vendored Textarea is a
    // form primitive used well away from any composer, so a bare
    // `[data-slot="textarea"]` rule retypes every form in the app.
    for (const shared of ['[data-slot="textarea"]', '[data-slot="badge"]', ".aui-composer-input"]) {
      for (const { selector } of COMPOSER) {
        if (!selector.includes(shared)) continue;
        expect(selector, `${shared} is restyled by: ${selector}`).toMatch(
          /\[data-slot="composer[^"]*"\](?:\s+|:has\()/,
        );
      }
    }
  });

  it("reaches ALL THREE text entries, not just the slotted ones", () => {
    // The workspace composer mounts `ComposerPrimitive.Input`, which exports no
    // `data-slot` at all — so a slot-only seam is correct on the landing page
    // and silently absent on the transcript, which is indistinguishable from
    // working unless the census is taken.
    const sized = COMPOSER.filter(({ body }) => /font-size:\s*var\(--text-base\)/.test(body));
    const placeheld = COMPOSER.filter(({ selector }) => selector.includes("::placeholder"));
    const focused = COMPOSER.filter(({ selector }) => selector.includes(":focus-visible"));
    for (const entry of TEXT_ENTRIES) {
      for (const [what, rules] of [
        ["the ramp step", sized],
        ["a placeholder tier", placeheld],
        ["a focus indicator", focused],
      ] as const) {
        expect(
          rules.some(({ selector }) => selector.includes(entry)),
          `${entry} never receives ${what}`,
        ).toBe(true);
      }
    }
  });
});

describe("the composer theme seam speaks in TOKENS", () => {
  it("takes every font-size from the ramp, never a pixel literal", () => {
    const sized = COMPOSER.filter(({ body }) => /font-size:/.test(body));
    expect(sized.length, "no composer font-size rules found").toBeGreaterThan(0);
    for (const { selector, body } of sized) {
      for (const match of body.matchAll(/font-size:\s*([^;]+);/g)) {
        expect(match[1]!.trim(), `${selector} sizes text as ${match[1]}`).toMatch(
          /^var\(--text-(xs|sm|base)\)$/,
        );
      }
    }
  });

  it("pairs every ramp step with its OWN line height", () => {
    // A ramp size carrying the neighbouring step's leading is the subtler half
    // of the same bug: the size tracks the root and the rhythm does not, so the
    // chip's two lines drift apart under zoom.
    for (const { selector, body } of COMPOSER) {
      const step = body.match(/font-size:\s*var\(--text-([a-z0-9]+)\)/)?.[1];
      if (step === undefined) continue;
      expect(body, `${selector} sets --text-${step} without its line height`).toContain(
        `line-height: var(--text-${step}--line-height)`,
      );
    }
  });

  it("colours composer text from a TOKEN, never an alpha or a palette hue", () => {
    // §2: an alpha over the foreground means a different thing on every rung it
    // lands on, and upstream writes both `text-foreground/35` and a raw
    // `text-red-600/80`. The tiers plus `--destructive` are the whole vocabulary
    // this seam needs.
    for (const { selector, body } of COMPOSER) {
      for (const match of body.matchAll(/(?<![\w-])color:\s*([^;]+);/g)) {
        expect(match[1]!.trim(), `${selector} colours text as ${match[1]}`).toMatch(
          /^var\(--(content-(primary|secondary|tertiary)|foreground|destructive)\)$/,
        );
      }
    }
  });

  it("gives the shelf the sunken rung and its OWN hairline, quieter than the bar's", () => {
    // Two outlines, not one: the shelf takes the decorative tier and the bar
    // above it takes a stronger mix, so the writing surface stays the anchor.
    const shelf = classBody(".composer-shelf");
    expect(shelf, ".composer-shelf is missing").not.toBeNull();
    expect(shelf).toContain("var(--surface-sunken)");
    expect(shelf).toMatch(/border:\s*1px solid var\(--border\)/);
    const bar = RULES.find((rule) => rule.selector === '[data-slot="composer-bar"]' && /border-color:/.test(rule.body));
    expect(bar?.body, "the composer bar has no outline of its own").toMatch(/var\(--border-source\) 36%/);
    expect(shelf).toMatch(/border-radius:\s*var\(--radius-lg\)/);
    expect(shelf, "the inset is the caller's").not.toMatch(
      /\b(margin|padding|width|height|inset)\b/,
    );
  });

  it("marks editor focus on the bar itself, one quiet tier up", () => {
    // A focused text field always matches `:focus-visible` and both composers
    // autofocus, so a `--ring` outline here was the bar's RESTING look — the
    // loud perimeter. The caret carries focus; the bar's own line steps up to
    // the control tier, an opaque token rather than an alpha. `:not(...)` is
    // the ghost rule stepping ASIDE for focus, so only a positive match counts.
    const focus = COMPOSER.filter(
      ({ selector }) => selector.includes(":focus-visible") && !selector.includes(":not(:focus-visible)"),
    );
    expect(focus.length, "the bar draws no focus indicator").toBeGreaterThan(0);
    for (const { selector, body } of focus) {
      expect(selector, selector).toContain('[data-slot="composer-bar"]');
      expect(body, selector).toMatch(/border-color:\s*var\(--ring\)/);
      expect(body, `${selector} must not resize the bar`).not.toMatch(/border(-width)?:\s*\d/);
    }
  });

  it("suppresses the nested field's own box wherever it removes its border", () => {
    // One box, not two. Dropping the border but keeping the shadow leaves a
    // floating inner rectangle — the same defect wearing another property.
    const stripped = COMPOSER.filter(({ body }) => /border:\s*0/.test(body));
    expect(stripped.length, "the nested field keeps its own border").toBeGreaterThan(0);
    for (const { selector, body } of stripped) {
      expect(body, selector).toMatch(/box-shadow:\s*none/);
    }
  });

  it("floors the pointer targets in PIXELS, which is the one honest literal", () => {
    // §1: text size and hit area are separate decisions. A pointer's physical
    // size does not shrink when type gets denser, so this floor must NOT track
    // the density root — the opposite of every other rule in this file.
    const floored = COMPOSER.filter(({ body }) => /min-(width|height):/.test(body));
    const covered = floored.map(({ selector }) => selector);
    for (const slot of ["composer-send", "composer-attach", "composer-voice-button"]) {
      expect(covered, `${slot} has no pointer floor`).toContain(`[data-slot="${slot}"]`);
    }
    // The file row's remove control is the smallest target in the composer and
    // the vendored File element gives it no slot, so its floor lives at the
    // call site — pinned in `attachment-file.test.tsx`, not here.
    for (const { selector, body } of floored) {
      expect(body, selector).toMatch(/min-width:\s*24px/);
      expect(body, selector).toMatch(/min-height:\s*24px/);
    }
  });
});

describe("the decorative brand field is decoration and nothing else", () => {
  const FIELD = classBody(".launch-brand-field");

  it("exists and draws from the ladder's edge token, adding no palette", () => {
    expect(FIELD, ".launch-brand-field is missing").not.toBeNull();
    expect(FIELD).toContain("var(--surface-edge)");
    // A hex, a palette hue or a bare colour function here is a new palette
    // entry smuggled in as decoration — what `lint:styling` stops Grove code
    // doing and what the design system's ADD table exists to make deliberate.
    expect(FIELD).not.toMatch(/#[0-9a-fA-F]{3,8}/);
    expect(FIELD).not.toMatch(/\boklch\(|\bhsl\(/);
  });

  it("is STATIC — no animation, no transition", () => {
    // It could only stand down for `prefers-reduced-motion` if a caller owned
    // its clock. There is no clock, so there is nothing to stand down.
    expect(FIELD).not.toMatch(/\banimation\b|\btransition\b|@keyframes/);
  });

  it("cannot swallow a pointer, and says so in CSS rather than at the caller", () => {
    // It covers the brand mark and the headline; eating a click on either is a
    // defect, not a variant. `aria-hidden` stays markup's — the a11y tree is.
    expect(FIELD).toMatch(/pointer-events:\s*none/);
  });

  it("scales its cell off the ramp, so it tracks density and zoom with the type", () => {
    // A rem literal would hold still while every neighbour moved under the
    // density root; the generation loader's argument, applied to a background.
    expect(FIELD).toMatch(/background-size:[^;]*var\(--text-(xs|sm|base)\)/);
  });

  it("fades out rather than ending on an edge", () => {
    // A hard-edged field reads as a bordered box behind the mark — a second
    // surface the ladder never placed. The mask is what makes it a field.
    expect(FIELD).toMatch(/mask-image:\s*radial-gradient/);
  });
});

describe("the composer controls are GHOSTED", () => {
  /** Every toolbar rule that touches a control's paint. */
  const painted = RULES.filter(
    ({ selector, body }) =>
      selector.includes('[data-slot="composer-toolbar"]') &&
      /(?:^|;|\s)(?:border(?:-color)?|background(?:-color|-image)?):/.test(body),
  );

  it("paints attach and the model trigger ONLY inside a composer toolbar", () => {
    // SCOPE. `model-selector-trigger` is a standalone element — the create
    // dialog mounts one away from any composer, so an unscoped rule repaints a
    // control that is not on a toolbar at all.
    const touching = RULES.filter(
      ({ selector, body }) =>
        (selector.includes("composer-attach") || selector.includes("model-selector-trigger")) &&
        /(?:^|;|\s)(?:border|background(?:-color|-image)?):/.test(body),
    );
    expect(touching.length, "no control paint rules found at all").toBeGreaterThan(0);
    for (const { selector } of touching) {
      expect(selector, `${selector} paints a control outside a composer toolbar`).toContain(
        '[data-slot="composer-toolbar"] ',
      );
    }
  });

  it("draws no edge and no gradient on a resting control — the bar is the one line", () => {
    expect(painted.length, "no toolbar paint rules found").toBeGreaterThan(0);
    for (const { selector, body } of painted) {
      expect(body, `${selector} draws a gradient`).not.toMatch(/background-image:/);
      expect(body, `${selector} draws an edge`).not.toMatch(/border:\s*1px/);
    }
  });

  it("leaves SEND alone — it is the one ink-filled control on the bar", () => {
    for (const { selector } of painted) {
      expect(selector, `${selector} repaints send`).not.toContain("composer-send");
    }
  });

  it("gives the open menu its elevation in BOTH themes, not just light", () => {
    // §4.3: overlay is the one rung that carries a shadow in both themes. The
    // vendored content ships `shadow-md`, tuned for a white page.
    const lit = RULES.filter(
      ({ selector, body }) =>
        selector.includes('[data-slot="model-selector-content"]') && /box-shadow:/.test(body),
    );
    expect(
      lit.some(({ selector }) => !selector.includes(".dark")),
      "the open model menu has no shadow in light",
    ).toBe(true);
    expect(
      lit.some(({ selector }) => /^\.dark\b/.test(selector)),
      "the open model menu has no shadow in dark",
    ).toBe(true);
  });
});
