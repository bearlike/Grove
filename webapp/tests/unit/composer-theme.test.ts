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
  ({ selector }) => selector.includes("composer") || selector.includes("launch-suggestion"),
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

/**
 * The body of the rule that DECLARES a selector, or null where it is absent.
 *
 * "Declares" rather than "first mentions": a selector also appears inside the
 * reduced-motion block, whose body is only `transition: none`, and that is
 * not the rule any assertion here is about.
 */
function classBody(name: string): string | null {
  const bodies = RULES.filter(({ selector }) => selector === name).map(({ body }) => body);
  return bodies.find((body) => !/^\s*(transition|animation):\s*none;\s*$/.test(body)) ?? bodies[0] ?? null;
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
          /^var\(--text-(xs|sm|base|lg)\)$/,
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
        // `oklch(1 0 0)` is the ladder's one permitted chroma-zero top (§4.2):
        // the ink on the accent-filled send and stop controls.
        expect(match[1]!.trim(), `${selector} colours text as ${match[1]}`).toMatch(
          /^(var\(--(content-(primary|secondary|tertiary)|foreground|destructive)\)|oklch\(1 0 0\))$/,
        );
      }
    }
  });

  it("attaches the shelf as a quieter fill beneath the raised bar", () => {
    const shelf = classBody(".composer-shelf");
    expect(shelf).not.toBeNull();
    expect(shelf).toContain("var(--surface-raised) 70%, var(--surface-base)");
    expect(shelf).toMatch(/border:\s*0/);
    expect(shelf).toMatch(/border-radius:\s*0 0 /);
    // Tucked under the bar: inset by the bar's 24px corner, pulled up behind
    // its bottom edge, and padded back down so the controls clear the bar.
    expect(shelf).toMatch(/margin-inline:\s*24px/);
    expect(shelf).toMatch(/margin-top:\s*-24px/);
    expect(shelf).toMatch(/padding:\s*calc\(24px \+/);
    expect(classBody('[data-slot="composer"] > [data-slot="composer-bar"]')).toMatch(/z-index:\s*1/);
    const bar = classBody('[data-slot="composer-bar"]');
    expect(bar).toContain("background-color: var(--surface-raised)");
    expect(bar).toContain("border-color: var(--border)");
  });

  it("marks editor focus on the bar itself, one quiet tier up", () => {
    // A focused text field always matches `:focus-visible` and both composers
    // autofocus, so a `--ring` outline here was the bar's RESTING look — the
    // loud perimeter. The caret carries focus; the bar's own line steps only
    // HALFWAY toward `--ring` from its resting 43% edge. `:not(...)` is the
    // ghost rule stepping ASIDE for focus, so only a positive match counts.
    const focus = COMPOSER.filter(
      ({ selector }) => selector.includes(":focus-visible") && !selector.includes(":not(:focus-visible)"),
    );
    expect(focus.length, "the bar draws no focus indicator").toBeGreaterThan(0);
    for (const { selector, body } of focus) {
      expect(selector, selector).toContain('[data-slot="composer-bar"]');
      expect(body, selector).toMatch(
        /border-color:\s*color-mix\(in oklab, var\(--ring\) 50%, var\(--border\)\)/,
      );
      expect(body, `${selector} must not resize the bar`).not.toMatch(/border(-width)?:\s*\d/);
    }
  });

  it("suppresses the nested field's own box wherever it removes its border", () => {
    // One box, not two. Dropping the border but keeping the shadow leaves a
    // floating inner rectangle — the same defect wearing another property.
    const stripped = COMPOSER.filter(({ selector, body }) =>
      selector.includes('[data-slot="textarea"]') && /border:\s*0/.test(body),
    );
    expect(stripped.length, "the nested field keeps its own border").toBeGreaterThan(0);
    for (const { selector, body } of stripped) {
      expect(body, selector).toMatch(/box-shadow:\s*none/);
    }
  });

  it("floors the pointer targets in PIXELS, which is the one honest literal", () => {
    // §1: text size and hit area are separate decisions. A pointer's physical
    // size does not shrink when type gets denser, so this floor must NOT track
    // the density root — the opposite of every other rule in this file.
    const floored = COMPOSER.filter(({ selector, body }) =>
      /^\[data-slot="composer-(send|attach)"\]$/.test(selector) && /min-(width|height):/.test(body),
    );
    const covered = floored.map(({ selector }) => selector);
    for (const slot of ["composer-send", "composer-attach"]) {
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

describe("the landing page has NO decorative field", () => {
  it("draws no grid behind the greeting", () => {
    // Removed on request. A rule left in the theme would paint it again the
    // moment a caller spelled the class, so its absence is what is pinned.
    expect(classBody(".launch-brand-field")).toBeNull();
    expect(CSS).not.toMatch(/launch-grid-line/);
  });

  it("gives each suggestion card the composer's plane and corner, with a hover lift", () => {
    const card = classBody(".launch-suggestion");
    expect(card).toMatch(/background-color:\s*var\(--surface-raised\)/);
    expect(card).toMatch(/border-radius:\s*24px/);
    expect(classBody(".launch-suggestion:hover")).toMatch(/var\(--accent\)/);
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

  it("fills SEND with the accent through its own slot, never a toolbar rule", () => {
    for (const { selector } of painted) {
      expect(selector, `${selector} repaints send`).not.toContain("composer-send");
    }
    expect(classBody('[data-slot="composer-send"]:not(:disabled)')).toMatch(/var\(--info\)/);
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

describe("the model and effort marks are LARGE in every picker", () => {
  // The vendored selector boxes its icon at `size-3.5` (11.2px at this root),
  // which read as a smudge beside the model's name. The theme lifts the mark
  // in the trigger and in every menu row by the mark's own identity, so no
  // call site re-decides it — and a `size-3.5` left at a call site is the
  // override that would put one picker back to a smudge.
  const sized = RULES.filter(
    ({ selector, body }) =>
      /\[data-testid="(model|effort)-mark"\]/.test(selector) && /(width|height|size)/.test(body),
  );

  it("sizes both marks inside the trigger and inside the rows", () => {
    for (const mark of ["model-mark", "effort-mark"]) {
      for (const host of ["model-selector-trigger", "model-selector-item"]) {
        expect(
          sized.some(({ selector }) => selector.includes(`[data-slot="${host}"]`) && selector.includes(mark)),
          `${mark} is not sized inside ${host}`,
        ).toBe(true);
      }
    }
  });

  it("no call site shrinks a mark back down", () => {
    for (const file of [
      "components/grove/launch/controls/model-pill.tsx",
      "components/grove/launch/controls/effort-pill.tsx",
      "components/grove/workspace/composer-model.tsx",
      "components/grove/workspace/composer-effort.tsx",
    ]) {
      expect(readFileSync(file, "utf8"), file).not.toMatch(/Mark[^/]*className="size-3\.5"/);
    }
  });
});

describe("effort levels wear their own hue", () => {
  // A sequential scale (§4.1): the hue rises with the effort — blue, green,
  // yellow, orange, orange — keyed on the mark's `data-level` in the theme,
  // so every picker and trigger agree without a call site naming a colour.
  const expected: Record<string, string> = {
    low: "--effort-low",
    medium: "--effort-medium",
    high: "--effort-high",
    xhigh: "--effort-xhigh",
    max: "--effort-max",
  };
  for (const [level, token] of Object.entries(expected)) {
    it(`${level} is coloured by ${token}`, () => {
      const rule = classBody(`[data-testid="effort-mark"][data-level="${level}"]`);
      expect(rule, `${level} has no colour rule`).not.toBeNull();
      expect(rule).toContain(`color: var(${token})`);
    });
  }

  it("names the five hues once, in both themes, as blue, green, yellow, orange, orange", () => {
    const root = CSS.slice(0, CSS.indexOf(".dark {"));
    const dark = CSS.slice(CSS.indexOf(".dark {"));
    for (const theme of [root, dark]) {
      const hue = (name: string) => Number(theme.match(new RegExp(`--effort-${name}: oklch\\(\\S+ \\S+ ([\\d.]+)\\)`))?.[1]);
      expect(hue("low")).toBeGreaterThan(230); // blue
      expect(hue("low")).toBeLessThan(280);
      expect(hue("medium")).toBeGreaterThan(135); // green
      expect(hue("medium")).toBeLessThan(175);
      expect(hue("high")).toBeGreaterThan(85); // yellow
      expect(hue("high")).toBeLessThan(110);
      expect(hue("xhigh")).toBeGreaterThan(45); // orange
      expect(hue("xhigh")).toBeLessThan(75);
      expect(hue("max")).toBe(hue("xhigh"));
    }
  });
});
