/*
 * Mermaid rendering for the shadcn theme.
 *
 * Three jobs, in order:
 *   1. Configure mermaid from the LIVE theme tokens so a diagram is drawn in
 *      the page's own palette and typeface rather than mermaid's stock one.
 *   2. Render every `pre.mermaid` fence into a framed, click-to-expand card.
 *   3. Provide the expanded view: a native <dialog> stage with zoom, pan and
 *      fit-to-stage, matching the reading affordances the Mewbo console gives
 *      the same diagrams.
 *
 * The fence markup comes from pymdownx.superfences `custom_fences` with
 * `class: mermaid`, i.e. `<pre class="mermaid"><code>…</code></pre>`. Without
 * that extension configured, a ```mermaid block is highlighted as plain text
 * and nothing here matches — which is silent, so the demo site carries a
 * fixture page to keep it exercised.
 */

/* ── Theme tokens ─────────────────────────────────────────────────────────
 * Mermaid does not consume CSS variables. Its layout engine measures text on
 * a Canvas 2D context and it hands colours to an internal colour library, so
 * both have to arrive as already-resolved literal values.
 *
 * Colours are resolved THROUGH A CANVAS rather than passed along verbatim.
 * The theme's tokens are authored in `oklch()`, which mermaid's colour library
 * does not parse; painting the computed value onto a 1x1 canvas and reading
 * the pixel back delegates parsing to the browser and yields a plain hex
 * string that every mermaid version understands. That also means this keeps
 * working if the tokens are ever re-authored in another colour space.
 */

const COLOR_PROBE = (() => {
  let ctx = null;
  return () => {
    if (ctx) return ctx;
    const canvas = document.createElement("canvas");
    canvas.width = 1;
    canvas.height = 1;
    ctx = canvas.getContext("2d", { willReadFrequently: true });
    return ctx;
  };
})();

const toHex = (value, fallback) => {
  const ctx = COLOR_PROBE();
  if (!ctx || !value) return fallback;
  try {
    // Painting over an opaque base keeps a translucent token (the theme's
    // hairline borders carry alpha) from reading back as near-transparent
    // black once mermaid bakes it into the SVG as a flat stroke.
    ctx.clearRect(0, 0, 1, 1);
    ctx.fillStyle = "#000000";
    ctx.fillStyle = value;
    // A value the browser cannot parse leaves fillStyle at the previous
    // assignment, so an unparseable token falls back rather than painting black.
    if (ctx.fillStyle === "#000000" && value.trim() !== "#000000") {
      const probe = ctx.fillStyle;
      if (probe === "#000000") return fallback;
    }
    ctx.fillRect(0, 0, 1, 1);
    const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data;
    return (
      "#" +
      [r, g, b].map((c) => c.toString(16).padStart(2, "0")).join("")
    );
  } catch {
    return fallback;
  }
};

const tokenColor = (name, fallback) => {
  const raw = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
  return toHex(raw, fallback);
};

/**
 * Mermaid feeds this string straight into `ctx.font` to measure label text
 * while it lays the diagram out, so it must be a resolved stack and not a
 * `var(--font-sans)` reference — that resolves in a stylesheet but not on a
 * canvas, and the silent result is every node sized for the wrong typeface.
 */
const tokenFont = () => {
  const raw = getComputedStyle(document.documentElement)
    .getPropertyValue("--font-sans")
    .trim();
  return raw || "system-ui, sans-serif";
};

const isDarkMode = () => document.documentElement.classList.contains("dark");

const mermaidConfig = () => {
  const dark = isDarkMode();
  const fontFamily = tokenFont();
  return {
    startOnLoad: false,
    securityLevel: "loose",
    fontFamily,
    // `base` is the ONLY theme mermaid lets themeVariables modify. Under
    // `default` or `dark` it computes its own palette and silently discards
    // the overrides — which renders a diagram in mermaid's stock lavender no
    // matter how carefully the tokens below are resolved. `darkMode` is what
    // tells `base` which direction to derive its remaining shades in.
    theme: "base",
    themeVariables: {
      darkMode: dark,
      // Every colour below comes from the `--diagram-*` family, NOT from the
      // chrome tokens. Chrome is tuned to recede; a diagram has to be read.
      // Pointing `primaryColor` at `--card` is what produced a node filled
      // with the exact colour of the card behind it. See the "Diagram tokens"
      // block in mewbo.css for the measured ratios.
      background: tokenColor("--diagram-surface", dark ? "#121110" : "#f2f0ea"),
      primaryColor: tokenColor("--diagram-node-bg", dark ? "#26241f" : "#ffffff"),
      primaryTextColor: tokenColor("--diagram-label", dark ? "#f8f8f6" : "#0a0a0a"),
      primaryBorderColor: tokenColor("--diagram-node-border", dark ? "#767269" : "#8c8880"),
      lineColor: tokenColor("--diagram-line", dark ? "#8d8a80" : "#6f6c64"),
      // Left unstated, `base` derives these by rotating the primary hue, so
      // subgraphs and alternating rows come out in colours that appear
      // nowhere else on the site. Point them at real surface tokens instead.
      secondaryColor: tokenColor("--diagram-cluster", dark ? "#1c1b18" : "#e7e4db"),
      tertiaryColor: tokenColor("--diagram-surface", dark ? "#121110" : "#f2f0ea"),
      secondaryBorderColor: tokenColor("--diagram-node-border", dark ? "#767269" : "#8c8880"),
      tertiaryBorderColor: tokenColor("--diagram-node-border", dark ? "#767269" : "#8c8880"),
      secondaryTextColor: tokenColor("--diagram-label", dark ? "#f8f8f6" : "#0a0a0a"),
      tertiaryTextColor: tokenColor("--diagram-label", dark ? "#f8f8f6" : "#0a0a0a"),
      // Mermaid derives the edge-label chip fill from its OWN palette rather
      // than from `background`, so leaving this unset renders a flowchart
      // branch's yes/no label as grey-on-grey against a dark diagram.
      edgeLabelBackground: tokenColor("--diagram-surface", dark ? "#121110" : "#f2f0ea"),
      textColor: tokenColor("--diagram-label", dark ? "#f8f8f6" : "#0a0a0a"),
      nodeTextColor: tokenColor("--diagram-label", dark ? "#f8f8f6" : "#0a0a0a"),
      mainBkg: tokenColor("--diagram-node-bg", dark ? "#26241f" : "#ffffff"),
      nodeBorder: tokenColor("--diagram-node-border", dark ? "#767269" : "#8c8880"),
      clusterBkg: tokenColor("--diagram-cluster", dark ? "#1c1b18" : "#e7e4db"),
      clusterBorder: tokenColor("--diagram-node-border", dark ? "#767269" : "#8c8880"),
      titleColor: tokenColor("--diagram-label", dark ? "#f8f8f6" : "#0a0a0a"),
      fontFamily,
    },
    flowchart: {
      // HTML labels render as foreignObject, which is what makes wrapped and
      // multi-line label text stylable from CSS (`.nodeLabel`) at all. With
      // htmlLabels off, labels are <text>/<tspan> and no amount of CSS fixes
      // their line spacing.
      htmlLabels: true,
      // Wider labels wrap onto fewer lines, so a node grows sideways instead
      // of downwards. This is the honest lever for the "prefer horizontal
      // growth" goal: mermaid has no theme-side control over rank direction
      // (TD vs LR lives in the author's source), but it does control how much
      // text sits on one line before wrapping.
      wrappingWidth: 260,
      nodeSpacing: 45,
      rankSpacing: 55,
      padding: 12,
      curve: "basis",
      useMaxWidth: true,
    },
    sequence: { useMaxWidth: true, wrap: true },
    class: { useMaxWidth: true },
    gantt: { useMaxWidth: true },
  };
};

/* ── Render cache ─────────────────────────────────────────────────────────
 * Keyed `theme|source`. A theme flip and back is free, and opening the
 * expanded view never re-invokes mermaid for a diagram already on the page.
 */
const svgCache = new Map();

let initialisedTheme = null;

const ensureInitialised = () => {
  const theme = isDarkMode() ? "dark" : "light";
  if (initialisedTheme !== theme) {
    window.mermaid.initialize(mermaidConfig());
    initialisedTheme = theme;
  }
  return theme;
};

let renderSeq = 0;

const renderToSvg = async (source) => {
  const theme = ensureInitialised();
  const key = `${theme}|${source}`;
  const hit = svgCache.get(key);
  if (hit) return hit;
  const { svg } = await window.mermaid.render(
    `mmd-${Date.now()}-${renderSeq++}`,
    source
  );
  svgCache.set(key, svg);
  return svg;
};

/* ── Inline card ──────────────────────────────────────────────────────────
 * A diagram is a figure with an expand control, not a bare SVG: it gets the
 * same framed treatment as content images so it reads as a deliberate object
 * on the page. The whole card is the click target; the button is the visible
 * affordance and the keyboard path.
 */

const EXPAND_ICON =
  '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 3h6v6"/><path d="M9 21H3v-6"/><path d="M21 3l-7 7"/><path d="M3 21l7-7"/></svg>';

// Keep 16px diagram labels at least 12px in a cropped preview. The stylesheet
// owns the floor (`--ms-diagram-natural` * 0.75 in mewbo.css); the viewer
// below reuses this constant for its own minimum zoom.
const LEGIBLE_SCALE = 0.75;

// How far the declared viewBox may exceed the drawn content before it is
// treated as wrong rather than as padding. Mermaid's own margin is a few
// percent; the failures this guards against are multiples.
const VIEWBOX_TRUST_RATIO = 1.5;

/**
 * The diagram's true size in SVG units.
 *
 * THE viewBox IS NOT ALWAYS TRUSTWORTHY, which this used to assume. On some
 * displays mermaid emits a viewBox far larger than what it actually drew —
 * measured on a reader's 32" monitor at `devicePixelRatio: 1.1875`, a diagram
 * whose content is 1027x72 was handed a viewBox of 2703x2652: 2.6x too wide
 * and 37x too tall, i.e. 97% of the declared area was empty. Every consumer of
 * this number then did correct arithmetic on a fiction:
 *
 *   - the CSS legibility floor (`--ms-diagram-natural` * 0.75) demanded 2027px
 *     inside a 990px stage, so the diagram was clipped 519px off its left edge
 *   - `height: auto` against a 2652-tall box made the SVG 1989px tall in a
 *     512px stage, which is the tall empty card a reader actually sees
 *   - the viewer's fit computed 0.112 and clamped to MIN_SCALE, opening the
 *     diagram at minimum zoom
 *
 * All three read as different bugs and are one bad number. So the viewBox is
 * now CHECKED against the rendered content's own bounding box rather than
 * believed: `getBBox()` reports what was drawn, in the same user-space units,
 * and disagreement beyond a small margin means the viewBox is the wrong one.
 *
 * getBBox() is preferred only when it disagrees, not always: it is the more
 * expensive call, it forces layout, and on a diagram with no drawn content it
 * legitimately returns zero — in which case the viewBox is all there is.
 */
const naturalSize = (svg) => {
  const box = svg.viewBox && svg.viewBox.baseVal;
  const declared = parseFloat(svg.style.maxWidth || "");

  let content = null;
  try {
    // The SVG ROOT, not `firstElementChild`: mermaid emits `<style>` first, and
    // a `<style>` element has no getBBox at all. Measuring the root reports the
    // union of everything drawn, which is the number wanted here anyway.
    const bbox = svg.getBBox && svg.getBBox();
    if (bbox && bbox.width > 0 && bbox.height > 0) {
      // getBBox() measures the ink; the viewBox usually carries a small margin
      // around it. Re-add the offset so a diagram is not cropped to its own
      // outermost stroke.
      content = {
        w: bbox.width + Math.max(0, bbox.x) * 2,
        h: bbox.height + Math.max(0, bbox.y) * 2,
      };
    }
  } catch {
    // getBBox() throws on a detached or display:none subtree. The viewBox
    // below is the fallback, which is the old behaviour.
  }

  if (box && box.width > 0 && box.height > 0) {
    const inflated =
      content &&
      (box.width > content.w * VIEWBOX_TRUST_RATIO ||
        box.height > content.h * VIEWBOX_TRUST_RATIO);
    if (inflated) return content;
    return { w: box.width, h: box.height };
  }

  if (content) return content;
  const rect = svg.getBoundingClientRect();
  return { w: declared || rect.width, h: rect.height };
};

/**
 * Hand the stylesheet the one number it cannot read for itself.
 *
 * The SVG's intrinsic width lives in its viewBox, which CSS cannot reach, so
 * it is published as `--ms-diagram-natural` and mewbo.css derives the whole
 * layout from it — fill the column, never below the legibility floor. Mermaid
 * also writes a `max-width` onto its own root; clearing it here means the
 * stylesheet's `width: 100%` is not fighting an inline declaration.
 *
 * This runs once per render. Nothing re-measures on resize, because a
 * percentage and a `calc()` already respond to one, which is the point: the
 * previous version recomputed pixel widths from a ResizeObserver, and any
 * delivery it missed left the diagram at its intrinsic size in the corner.
 */
const publishNaturalSize = (figure) => {
  const stage = figure.querySelector(".ms-mermaid__stage");
  const svg = stage && stage.querySelector("svg");
  if (!stage || !svg) return;
  const { w, h } = naturalSize(svg);

  // Correct the element itself when its own viewBox was the thing that was
  // wrong. Publishing a true width is not enough: an SVG scales its contents
  // to fit the viewBox, so a 37x-too-tall box renders the diagram as a small
  // mark adrift in a tall transparent canvas, and `height: auto` sizes the
  // element to that canvas. Rewriting the box makes the drawn content the
  // whole picture again, which is what makes the CSS below describe reality.
  const box = svg.viewBox && svg.viewBox.baseVal;
  if (
    box &&
    w > 0 &&
    h > 0 &&
    (box.width > w * VIEWBOX_TRUST_RATIO || box.height > h * VIEWBOX_TRUST_RATIO)
  ) {
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
    svg.removeAttribute("height");
  }

  if (w > 0) {
    stage.style.setProperty("--ms-diagram-natural", `${w}px`);
    // Also on the FIGURE, because the wide-screen rule that lets a diagram
    // escape the prose measure sizes the figure itself, and a custom property
    // set on the stage is not visible to its own parent. The padding is added
    // back so the width is the diagram's, not the diagram minus its frame.
    figure.style.setProperty(
      "--ms-diagram-natural-figure",
      `${Math.ceil(w) + 32}px`
    );
  }
  svg.style.removeProperty("max-width");
};

/**
 * Flag the two crop states so the stage can fade the cut edge and pin the
 * expand control open. Purely cosmetic: the layout above is already correct
 * without this, so a missed observer delivery now costs a fade rather than
 * stranding the diagram at the wrong size.
 */
const flagCropping = (figure) => {
  const stage = figure && figure.querySelector(".ms-mermaid__stage");
  const svg = stage && stage.querySelector("svg");
  if (!stage || !svg || !stage.clientWidth) return;
  const box = svg.getBoundingClientRect();
  figure.toggleAttribute("data-wide", box.width > stage.clientWidth + 1);
  figure.toggleAttribute("data-tall", box.height > stage.clientHeight + 1);
};

// Tabs, sidebar toggles and display changes all change the available width
// without a window resize, so the crop flags are re-evaluated on reveal too.
const cardResizeObserver = new ResizeObserver((entries) => {
  // Defer reads until the next frame so WebKit does not report an observer
  // delivery loop when a flag changes the stage's own mask.
  requestAnimationFrame(() => {
    for (const { target } of entries) flagCropping(target.parentElement);
  });
});

const buildCard = (source, svg, index) => {
  const figure = document.createElement("figure");
  figure.className = "ms-mermaid";
  figure.dataset.source = source;
  figure.dataset.diagramId = `diagram-${index + 1}`;

  const stage = document.createElement("div");
  stage.className = "ms-mermaid__stage";
  stage.innerHTML = svg;

  const expand = document.createElement("button");
  expand.type = "button";
  expand.className = "ms-mermaid__expand";
  expand.innerHTML = `${EXPAND_ICON}<span>Expand</span>`;
  expand.setAttribute("aria-label", "Expand diagram to full view");
  expand.title = "Expand diagram";

  figure.append(stage, expand);

  // NOT `publishNaturalSize(figure)` here. This figure is still DETACHED — the
  // caller inserts the returned node — and `getBBox()` on an SVG outside the
  // document throws, which sends naturalSize down its fallback and makes it
  // believe the very viewBox it exists to check. That is how a diagram whose
  // content is 1027x72 published 2703px as its natural width: the measurement
  // was correct code running at a moment when nothing could be measured.
  // `attachCard` below publishes once the node is live.
  cardResizeObserver.observe(stage);

  const open = () => openViewer(figure);
  expand.addEventListener("click", (e) => {
    e.stopPropagation();
    open();
  });
  figure.addEventListener("click", open);

  return figure;
};

const buildError = (source, message) => {
  const figure = document.createElement("figure");
  figure.className = "ms-mermaid ms-mermaid--error";
  figure.dataset.source = source;
  const note = document.createElement("p");
  note.className = "ms-mermaid__error";
  note.textContent = message;
  const pre = document.createElement("pre");
  pre.className = "ms-mermaid__source";
  pre.textContent = source;
  figure.append(note, pre);
  return figure;
};

const renderMermaid = () => {
  if (!window.mermaid) return;
  const blocks = Array.from(document.querySelectorAll("pre.mermaid code"));
  blocks.forEach((code, idx) => {
    const pre = code.parentElement;
    if (!pre) return;
    const source = code.textContent || "";
    // Reserve the slot synchronously so a slow render cannot leave a raw,
    // pygments-highlighted fence visible while mermaid loads.
    const placeholder = document.createElement("figure");
    placeholder.className = "ms-mermaid ms-mermaid--pending";
    placeholder.dataset.source = source;
    placeholder.innerHTML =
      '<p class="ms-mermaid__pending">Rendering diagram…</p>';
    pre.replaceWith(placeholder);

    renderToSvg(source)
      .then((svg) => attachCard(placeholder, buildCard(source, svg, idx)))
      .catch(() =>
        placeholder.replaceWith(
          buildError(source, "This diagram could not be rendered.")
        )
      );
  });
};

/**
 * Put a card in the document, THEN measure it.
 *
 * The order is the whole point. `getBBox()` is only meaningful for an element
 * that is in the document and rendered: on a detached node it throws, and
 * inside a `display: none` subtree it reports zeros. `naturalSize` treats both
 * as "cannot measure" and falls back to the declared viewBox — which is
 * exactly the number it was written to double-check, so a measurement taken
 * one line too early does not merely fail, it silently re-adopts the bug.
 *
 * Every caller that creates a card goes through here so the ordering is
 * structural rather than something each site has to remember.
 */
const attachCard = (placeholder, figure) => {
  placeholder.replaceWith(figure);
  publishNaturalSize(figure);
  return figure;
};

/* ── Expanded viewer ──────────────────────────────────────────────────────
 * One <dialog> for the whole page, reused per diagram — the same native
 * dialog + ::backdrop mechanism the search modal uses, so Escape, focus
 * trapping and the backdrop come from the platform rather than hand-rolled.
 */

const MIN_SCALE = 0.2;
const MAX_SCALE = 6;
const FIT_MARGIN = 48;

const viewer = {
  dialog: null,
  stage: null,
  inner: null,
  label: null,
  scale: 1,
  fitScale: 1,
  // Whether the open fit has already landed for the CURRENT diagram. Guards the
  // stage ResizeObserver so a late delivery cannot overwrite a reader's zoom.
  fitted: false,
  pos: { x: 0, y: 0 },
  drag: null,
};

const applyTransform = () => {
  if (!viewer.inner) return;
  viewer.inner.style.transform =
    `translate(calc(-50% + ${viewer.pos.x}px), calc(-50% + ${viewer.pos.y}px))` +
    ` scale(${viewer.scale})`;
};

const setScale = (next) => {
  viewer.scale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, next));
  applyTransform();
};

// Derive the fit from SVG coordinates, never an animated screen rectangle.
//
// REFUSES TO FIT AGAINST A STAGE THAT HAS NO SIZE. A closed <dialog> is
// `display: none`, so every rectangle inside it measures 0x0 — and this used to
// run in that state on any paint that resolved before `showModal()`, which is
// what a warm `svgCache` produces. The arithmetic then reads
// `(0 - FIT_MARGIN) / naturalW`, i.e. NEGATIVE, and the clamp turns that into
// MIN_SCALE: the diagram opened at 0.2 in the corner of an empty stage, looking
// for all the world like it had failed to render. It is a race, so it reproduced
// intermittently and never on a cold cache, which is why it survived v1.30.0.
//
// Bailing out is safe because it is no longer the only trigger: the
// ResizeObserver below fits the moment the stage HAS a size, so the deferred
// case lands one frame later instead of landing wrong.
// Returns whether a fit was actually applied, so a caller can tell "fitted"
// from "declined because there was nothing to measure" and arrange to try
// again rather than assume the diagram is placed.
const recomputeFit = () => {
  if (!viewer.stage || !viewer.inner) return false;
  const svg = viewer.inner.querySelector("svg");
  if (!svg) return false;
  const stageRect = viewer.stage.getBoundingClientRect();
  if (stageRect.width <= FIT_MARGIN || stageRect.height <= FIT_MARGIN) {
    return false;
  }
  const { w: naturalW, h: naturalH } = naturalSize(svg);
  if (naturalW <= 0 || naturalH <= 0) return false;
  const fit = Math.min(
    (stageRect.width - FIT_MARGIN) / naturalW,
    (stageRect.height - FIT_MARGIN) / naturalH
  );
  viewer.fitScale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, fit));
  viewer.pos = { x: 0, y: 0 };
  setScale(viewer.fitScale);
  return true;
};

const buildViewer = () => {
  if (viewer.dialog) return viewer.dialog;

  const dialog = document.createElement("dialog");
  dialog.className = "ms-diagram-viewer";
  dialog.innerHTML = `
    <div class="ms-diagram-viewer__bar">
      <span class="ms-diagram-viewer__label">${EXPAND_ICON}<span data-role="label"></span></span>
      <div class="ms-diagram-viewer__actions">
        <button type="button" data-act="out" aria-label="Zoom out" title="Zoom out">&minus;</button>
        <button type="button" data-act="reset" aria-label="Reset zoom" title="Reset zoom">&#8635;</button>
        <button type="button" data-act="in" aria-label="Zoom in" title="Zoom in">&plus;</button>
        <button type="button" data-act="close" aria-label="Close diagram" title="Close">&times;</button>
      </div>
    </div>
    <div class="ms-diagram-viewer__stage">
      <div class="ms-diagram-viewer__inner"></div>
    </div>
    <p class="ms-diagram-viewer__hint">Scroll to zoom · drag to pan · Esc to close</p>
  `;
  document.body.appendChild(dialog);

  viewer.dialog = dialog;
  viewer.stage = dialog.querySelector(".ms-diagram-viewer__stage");
  viewer.inner = dialog.querySelector(".ms-diagram-viewer__inner");
  viewer.label = dialog.querySelector("[data-role=label]");

  dialog.querySelector("[data-act=close]").addEventListener("click", () =>
    dialog.close()
  );
  dialog
    .querySelector("[data-act=in]")
    .addEventListener("click", () => setScale(viewer.scale * 1.1));
  dialog
    .querySelector("[data-act=out]")
    .addEventListener("click", () => setScale(viewer.scale * 0.9));
  dialog.querySelector("[data-act=reset]").addEventListener("click", () => {
    viewer.pos = { x: 0, y: 0 };
    setScale(viewer.fitScale);
  });

  viewer.stage.addEventListener(
    "wheel",
    (e) => {
      e.preventDefault();
      setScale(viewer.scale * (e.deltaY > 0 ? 0.92 : 1.08));
    },
    { passive: false }
  );

  viewer.stage.addEventListener("pointerdown", (e) => {
    viewer.drag = {
      x: e.clientX,
      y: e.clientY,
      sx: viewer.pos.x,
      sy: viewer.pos.y,
    };
    viewer.inner.style.transition = "none";
    viewer.stage.setPointerCapture(e.pointerId);
    viewer.stage.dataset.dragging = "1";
  });
  viewer.stage.addEventListener("pointermove", (e) => {
    if (!viewer.drag) return;
    viewer.pos = {
      x: viewer.drag.sx + (e.clientX - viewer.drag.x),
      y: viewer.drag.sy + (e.clientY - viewer.drag.y),
    };
    applyTransform();
  });
  const endDrag = () => {
    viewer.drag = null;
    viewer.inner.style.transition = "";
    delete viewer.stage.dataset.dragging;
  };
  viewer.stage.addEventListener("pointerup", endDrag);
  viewer.stage.addEventListener("pointercancel", endDrag);

  // Clicking the backdrop closes. The stage fills the dialog, so a click that
  // lands on the dialog element itself is necessarily outside the content.
  dialog.addEventListener("click", (e) => {
    if (e.target === dialog) dialog.close();
  });

  window.addEventListener("resize", () => {
    if (dialog.open) recomputeFit();
  });

  // The stage going from 0x0 (closed dialog) to its real size IS a resize, and
  // it is the delivery `recomputeFit` above cannot schedule for itself: a paint
  // that resolves before `showModal()` has nothing to measure. Observing the
  // stage makes the fit self-correcting rather than ordering-dependent — it
  // runs when there is something to fit, whether that is the open, a window
  // resize, or a theme flip that repaints while the dialog is already up.
  //
  // `viewer.fitted` keeps this from stealing a reader's zoom: once they have
  // scrolled or dragged, a later delivery must not yank the diagram back to
  // fit. It is cleared on each open, in `openViewer`.
  new ResizeObserver(() => {
    if (!dialog.open || viewer.fitted) return;
    viewer.fitted = recomputeFit();
  }).observe(viewer.stage);

  return dialog;
};

const paintViewer = (svg) => {
  viewer.inner.innerHTML = svg;
  const el = viewer.inner.querySelector("svg");
  if (el) {
    const { w, h } = naturalSize(el);
    // The viewer paints its own copy of the markup, so an inflated viewBox
    // arrives here uncorrected. Pinning width/height to the TRUE size while
    // leaving a 37x-too-tall viewBox in place would draw the diagram at a
    // fraction of that box and leave the rest transparent — the fit would be
    // arithmetically right and visibly wrong. Rewrite the box first, for the
    // same reason `publishNaturalSize` does.
    const box = el.viewBox && el.viewBox.baseVal;
    if (
      box &&
      w > 0 &&
      h > 0 &&
      (box.width > w * VIEWBOX_TRUST_RATIO ||
        box.height > h * VIEWBOX_TRUST_RATIO)
    ) {
      el.setAttribute("viewBox", `0 0 ${w} ${h}`);
    }
    // Percentage SVG dimensions have no reference in a shrink-to-fit wrapper.
    // Pin both axes on EVERY paint, including a theme refresh.
    el.style.maxWidth = "none";
    el.style.width = `${w}px`;
    el.style.height = `${h}px`;
  }
  // Fit now if the stage is already measurable, which it is whenever the
  // dialog was opened first. `recomputeFit` reports whether it actually fitted
  // — it declines a collapsed stage rather than fitting against nothing — so a
  // paint that still lands too early simply leaves the work to the stage
  // ResizeObserver. The diagram is fitted by whichever arrives first, and
  // `viewer.fitted` stops the other from doing it twice.
  viewer.fitted = recomputeFit();
  if (!viewer.fitted) {
    requestAnimationFrame(() => {
      if (!viewer.fitted) viewer.fitted = recomputeFit();
    });
  }
};

const openViewer = (figure) => {
  const source = figure.dataset.source || "";
  if (!source) return;
  const dialog = buildViewer();
  viewer.label.textContent = figure.dataset.diagramId || "Diagram";
  viewer.pos = { x: 0, y: 0 };
  viewer.scale = 1;
  // Re-arm the observer's one-shot fit for this diagram. Without this, the
  // second diagram opened in a session would inherit `fitted` from the first
  // and keep that diagram's scale.
  viewer.fitted = false;
  viewer.inner.innerHTML = "";

  dialog.dataset.source = source;
  const theme = isDarkMode();
  const paint = (svg) => {
    if (dialog.dataset.source === source && isDarkMode() === theme) {
      paintViewer(svg);
    }
  };

  // OPEN FIRST, PAINT SECOND. A closed <dialog> is `display: none`, so every
  // measurement inside it reads zero: `getBBox()` returns 0x0, which sends
  // `naturalSize` down its fallback and makes it trust the very viewBox it
  // exists to check, and the stage rect is 0x0, which is what `recomputeFit`
  // refuses to fit against. Painting a cache hit before `showModal()` hit both
  // at once — the diagram was sized from an inflated box AND never fitted, so
  // it opened somewhere outside the visible stage and the reader had to hunt
  // for it by scrolling and zooming out.
  //
  // This is the same defect as the inline card's (v1.35.1): a measurement
  // taken one line too early does not fail loudly, it silently adopts a wrong
  // number. Opening first costs nothing — the dialog is empty for one frame at
  // most, and only on a cache hit, which is the path that was already instant.
  if (!dialog.open) dialog.showModal();

  const cached = svgCache.get(`${isDarkMode() ? "dark" : "light"}|${source}`);
  if (cached) paint(cached);

  if (!cached) {
    renderToSvg(source)
      .then(paint)
      .catch(() => {
        viewer.inner.textContent = "This diagram could not be rendered.";
      });
  }
};

/* ── Theme flips ──────────────────────────────────────────────────────────
 * Re-render every card against the incoming palette. Cache hits make a flip
 * back to a theme already seen instant.
 */
const rerenderAll = () => {
  if (!window.mermaid) return;
  ensureInitialised();
  const theme = isDarkMode();
  const figures = Array.from(
    document.querySelectorAll("figure.ms-mermaid[data-source]")
  );
  figures.forEach((figure) => {
    const source = figure.dataset.source || "";
    const stage = figure.querySelector(".ms-mermaid__stage");
    if (!source || !stage) return;
    renderToSvg(source)
      .then((svg) => {
        if (isDarkMode() !== theme) return;
        stage.innerHTML = svg;
        // A re-render replaces the SVG, so republish its size and re-read the
        // crop state against the new one.
        publishNaturalSize(figure);
        flagCropping(figure);
      })
      .catch(() => {
        /* keep the diagram that is already on screen */
      });
  });
  if (viewer.dialog && viewer.dialog.open) {
    const source = viewer.dialog.dataset.source;
    renderToSvg(source)
      .then((svg) => {
        if (viewer.dialog.open && viewer.dialog.dataset.source === source &&
            isDarkMode() === theme) paintViewer(svg);
      })
      .catch(() => {});
  }
};

const observeThemeChanges = () => {
  if (!window.MutationObserver) return;
  let dark = isDarkMode();
  const observer = new MutationObserver(() => {
    if (dark === isDarkMode()) return;
    dark = isDarkMode();
    rerenderAll();
  });
  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["class"],
  });
};

const boot = () => {
  renderMermaid();
  observeThemeChanges();
};

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}
