// Full-screen image viewer for content images. Viewer.js does the viewing; this
// file decides WHAT is viewable, builds the gallery, and puts a visible
// affordance on the page. The library is loaded from the CDN ahead of this file
// (emitted when theme `lightbox: true`). If it never loads, images stay
// ordinary images and no affordance is shown, which is the same failure mode
// the carousel has.
//
// Viewer.js rather than a plain lightbox because the viewer owns its own
// chrome: the toolbar, the title and the navigation float *over* the picture
// instead of being laid out beside it. Chrome that takes up layout is what
// kept a phone showing a picture at under a third of its screen.
(() => {
  "use strict";

  /**
   * One content image, and everything the viewer needs to know about it.
   *
   * State plus the rules over that state. Eligibility, source and caption are
   * properties of the image, not of the binder, so they live here rather than
   * as free functions the binder happens to call.
   */
  class ImageTarget {
    static OPT_OUT = ".no-lightbox, [data-lightbox='false']";
    // An icon or a badge is not a picture worth a full screen.
    static MIN_EDGE = 96;

    constructor(el) {
      this.el = el;
    }

    static from(node) {
      const el = node && node.closest ? node.closest("img") : null;
      return el ? new ImageTarget(el) : null;
    }

    get src() {
      return this.el.currentSrc || this.el.src || "";
    }

    /** The figcaption if there is one, else the alt text. */
    get caption() {
      const fig = this.el.closest("figure");
      const cap = fig && fig.querySelector("figcaption");
      return (cap && cap.textContent.trim()) || this.el.getAttribute("alt") || "";
    }

    get rect() {
      return this.el.getBoundingClientRect();
    }

    get isEligible() {
      if (this.el.closest(ImageTarget.OPT_OUT)) return false;
      // An image that is already a link belongs to that link.
      if (this.el.closest("a")) return false;
      if (!this.src) return false;
      const r = this.rect;
      return r.width >= ImageTarget.MIN_EDGE && r.height >= ImageTarget.MIN_EDGE;
    }

    /** The slide strip this image belongs to, or null when it stands alone. */
    get carousel() {
      return this.el.closest(".swiper");
    }

    /**
     * Every distinct picture in the same carousel, in slide order, so the
     * viewer's arrows walk the strip instead of dead-ending on one slide.
     *
     * Deduplicated BY SOURCE because Swiper runs with `loop: true` and may
     * clone slides; without this the same picture is listed two or three
     * times and the arrows appear to stall on it.
     */
    gallery() {
      const strip = this.carousel;
      if (!strip) return [this];
      const seen = new Set();
      const out = [];
      strip.querySelectorAll(".swiper-slide img").forEach((node) => {
        const target = new ImageTarget(node);
        if (!target.src || seen.has(target.src)) return;
        seen.add(target.src);
        out.push(target);
      });
      return out.length ? out : [this];
    }
  }

  /**
   * The hover affordance. A single floating button reused across every image,
   * rather than one injected per image.
   *
   * Injecting a wrapper per image would mutate slide markup Swiper owns, and a
   * cursor change alone is not an affordance: it says nothing until you are
   * already hovering and it says nothing about what will happen.
   */
  class ZoomHint {
    static MARGIN = 10;

    constructor() {
      /** Set by the bootstrap once the viewers exist. */
      this.onActivate = () => {};
      this.target = null;
      this.el = this.build();
      this.track = this.track.bind(this);
    }

    build() {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "ms-zoom-hint";
      btn.setAttribute("aria-label", "View image full screen");
      btn.hidden = true;
      btn.innerHTML =
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" ' +
        'aria-hidden="true"><path d="M15 3h6v6"/><path d="M9 21H3v-6"/>' +
        '<path d="M21 3l-7 7"/><path d="M3 21l7-7"/></svg>';
      btn.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        if (this.target) this.onActivate(this.target);
      });
      document.body.appendChild(btn);
      return btn;
    }

    show(target) {
      this.target = target;
      this.el.hidden = false;
      this.place();
      window.addEventListener("scroll", this.track, { passive: true });
      window.addEventListener("resize", this.track, { passive: true });
    }

    place() {
      if (!this.target) return;
      const r = this.target.rect;
      this.el.style.top = `${r.top + ZoomHint.MARGIN}px`;
      this.el.style.left = `${r.right - this.el.offsetWidth - ZoomHint.MARGIN}px`;
    }

    track() {
      if (!this.target) return this.hide();
      const r = this.target.rect;
      // Scrolled out of view entirely, so the button has nothing to sit on.
      if (r.bottom < 0 || r.top > window.innerHeight) return this.hide();
      this.place();
    }

    hide() {
      this.el.hidden = true;
      this.target = null;
      window.removeEventListener("scroll", this.track);
      window.removeEventListener("resize", this.track);
    }

    owns(node) {
      return this.el.contains(node);
    }
  }

  /**
   * The off-document list Viewer.js reads its gallery from.
   *
   * Viewer.js builds a gallery by scanning a container for `<img>`, which is
   * how it is meant to be used but would mean pointing it at live page markup
   * — inside a carousel that means Swiper's clones and Swiper's own DOM
   * reordering. A detached `<ul>` we own instead: one entry per distinct
   * picture, built at open time and thrown away on close, so what the viewer
   * walks is exactly the gallery we computed and nothing the page does to its
   * slides can disturb it.
   */
  class GallerySource {
    constructor(targets) {
      this.targets = targets;
      this.el = this.build();
    }

    build() {
      const list = document.createElement("ul");
      list.className = "ms-viewer-source";
      list.hidden = true;
      this.targets.forEach((target) => {
        const item = document.createElement("li");
        const img = document.createElement("img");
        img.src = target.src;
        // Viewer.js titles a slide from `alt`, so the figcaption rides there.
        img.alt = target.caption;
        item.appendChild(img);
        list.appendChild(item);
      });
      document.body.appendChild(list);
      return list;
    }

    indexOf(target) {
      return Math.max(0, this.targets.findIndex((t) => t.src === target.src));
    }

    destroy() {
      this.el.remove();
    }
  }

  /**
   * Binds one content root. Collaborators are injected rather than constructed
   * here, so the viewer can be exercised without the library present.
   */
  class ImageViewer {
    static CHROME = ".swiper-button-prev, .swiper-button-next, .swiper-pagination";

    /**
     * How much of the available box an opened picture starts at.
     *
     * 0.8, which is a READABILITY decision rather than a layout budget. At 1
     * a landscape screenshot met every edge of the display, and with the page
     * hidden behind an opaque backdrop there was nothing left to say the
     * viewer was open: no margin, no frame, no visible boundary between the
     * picture and the browser. Readers could not tell whether they were
     * looking at an expanded image or at a page that happened to be an image,
     * and pressed Escape or Back to find out.
     *
     * A fifth of the smaller axis held back is enough for the backdrop to
     * read as a frame all the way round, while still opening the picture far
     * larger than it sits inline. The image is centred, so the reserve is
     * split evenly and no edge is special.
     *
     * NOT a return to the INSET_X / RESERVE_Y / MAX_EDGE budget this once
     * replaced. That subtracted a FLAT 200px of height and 144px of width for
     * chrome laid out beside the image, which on a 390x844 phone left the
     * picture under a third of the screen — the reserve did not scale, so the
     * smaller the display the worse the trade. A proportion cannot do that:
     * on that same phone 0.8 is a 78px inset, not 200px. The chrome still
     * floats (see the zero-height `.viewer-footer` rule in mewbo.css), so
     * nothing is reserved FOR it; this margin exists to be seen.
     *
     * Viewer.js still refuses to enlarge past an image's own pixels, so a
     * small screenshot opens at its own size and stays sharp.
     */
    static COVERAGE = 0.8;

    constructor(root, { factory, hint }) {
      this.root = root;
      this.factory = factory;
      this.hint = hint;
    }

    bind() {
      this.root.addEventListener("click", (event) => this.onClick(event));
      this.root.addEventListener("mouseover", (event) => this.onHover(event));
      this.root.addEventListener("mouseout", (event) => this.onLeave(event));
      this.mark();
      // Swiper mounts after this file runs and images decode later still, so
      // the affordance is re-applied once things settle.
      window.setTimeout(() => this.mark(), 600);
      window.addEventListener("load", () => this.mark(), { once: true });
    }

    /** Only images the click handler will actually open get the cursor. */
    mark() {
      this.root.querySelectorAll("img").forEach((node) => {
        const target = new ImageTarget(node);
        node.classList.toggle("ms-zoomable", target.isEligible);
      });
    }

    onClick(event) {
      if (event.target.closest(ImageViewer.CHROME)) return;
      const target = ImageTarget.from(event.target);
      if (!target || !target.isEligible) return;
      event.preventDefault();
      this.open(target);
    }

    onHover(event) {
      const target = ImageTarget.from(event.target);
      if (!target || !target.isEligible) return;
      this.hint.show(target);
    }

    onLeave(event) {
      const next = event.relatedTarget;
      if (next && (this.hint.owns(next) || ImageTarget.from(next))) return;
      this.hint.hide();
    }

    open(target) {
      this.hint.hide();
      const slides = target.gallery();
      const many = slides.length > 1;
      const source = new GallerySource(slides);
      const viewer = new this.factory(source.el, {
        className: "ms-viewer",
        initialViewIndex: source.indexOf(target),
        initialCoverage: ImageViewer.COVERAGE,
        // The picture is the point; the page behind it is not.
        backdrop: true,
        // A strip of thumbnails would be chrome competing with the image on a
        // phone, and the carousel it came from is already the thumbnail strip.
        navbar: false,
        // The caption, floating over the image rather than beside it. The
        // library's default title is `alt (W × H)`; a reader wants the
        // caption, not the pixel dimensions, so supply the text ourselves.
        title: (image) => image.alt || "",
        toolbar: {
          prev: many,
          next: many,
          zoomIn: true,
          zoomOut: true,
          oneToOne: true,
          reset: true,
          rotateLeft: true,
          rotateRight: true,
          flipHorizontal: false,
          flipVertical: false,
          play: false,
        },
        // Arrows walk the strip, so only offer them when there is a strip.
        loop: many,
        keyboard: true,
        // Never auto-advance: the reader opened this to look at one picture.
        autoplay: false,
        transition: true,
        tooltip: true,
        movable: true,
        zoomable: true,
        rotatable: true,
        scalable: false,
        slideOnTouch: many,
        toggleOnDblclick: true,
        zoomOnWheel: true,
        // Hand the OS the whole screen when the reader asks to fill it.
        fullscreen: true,
        hidden: () => {
          source.destroy();
          viewer.destroy();
        },
      });
      viewer.show();
    }
  }

  const start = (attempt = 0) => {
    if (typeof window.Viewer !== "function") {
      if (attempt > 40) return; // ~4s, then give up quietly
      window.setTimeout(() => start(attempt + 1), 100);
      return;
    }
    const roots = document.querySelectorAll("article, .md-content");
    if (!roots.length) return;
    const hint = new ZoomHint();
    const viewers = [...roots].map(
      (root) => new ImageViewer(root, { factory: window.Viewer, hint })
    );
    // Wired after construction rather than passed in, because the hint is
    // shared by every root and must not hold a reference to one of them.
    hint.onActivate = (target) => viewers[0].open(target);
    viewers.forEach((viewer) => viewer.bind());
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => start(), { once: true });
  } else {
    start();
  }
})();
