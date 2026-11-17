"use client";

/**
 * A PORT of `components/elements/mermaid-diagram.tsx`, kept diffable against it.
 *
 * GROVE DELTA 1 — the diagram is drawn by `mermaid` itself, not `beautiful-mermaid`.
 * Upstream swapped to beautiful-mermaid for bundle size and a synchronous render
 * (assistant-ui #4279). It is a from-scratch reimplementation with its own ELK
 * layout, and on the diagrams agents actually write — nested subgraphs, labelled
 * edges, fan-in — it places edge labels on top of edges, routes edges through
 * subgraph titles and lays the whole graph out as one wide band (its tracker:
 * lukilabs/beautiful-mermaid#138, #68, #89). Mermaid's own dagre layout is the
 * reference every other surface (GitHub, Gitea, docs sites) draws these with, so
 * a diagram looks the same here as in the PR body it was pasted into. The engine
 * is bigger, which is why it is loaded lazily and only when a fence appears; the
 * render is asynchronous and needs a document, so it runs in an effect and the
 * skeleton shows until it lands.
 *
 * GROVE DELTA 2 — colours are the surface ladder's tokens, resolved at render time.
 * Mermaid bakes colours into the SVG and does colour math on them, so it cannot
 * take `var(--background)`; `resolveColor` asks the browser for each token's
 * current value and the diagram re-renders when the theme flips.
 *
 * GROVE DELTA 3 — the zoom view renames the root id inside the SVG's own CSS too.
 * Upstream suffixes every `id` with `-zoom` so the two copies of the SVG never
 * collide, which was enough for beautiful-mermaid's inline `style=""`. Mermaid
 * instead scopes every rule as `#<root-id> .node rect {…}`, so after the rename
 * no rule matched and every shape fell back to SVG's default fill: solid black.
 *
 * GROVE DELTA 4 — the zoom viewport is a checkerboard of the `sunken` well on
 * `base`, the "transparent PNG" convention, so a diagram with no
 * background of its own reads as an object floating over the page rather than
 * a page of its own.
 *
 * `MermaidSvg` (CSP-safe style adoption), the rest of `MermaidZoom`, the
 * skeleton and the fallback are upstream's.
 */

import { Maximize2, Minus, Plus, RotateCcw, X } from "lucide-react";
import { useTheme } from "next-themes";
import {
  type FC,
  type ReactNode,
  memo,
  type ComponentProps,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { createPortal } from "react-dom";
import { cn } from "@/lib/utils";

export type MermaidDiagramProps = {
  code: string;
  className?: string;
  /** Renders a skeleton instead of the diagram while `true`. */
  streaming?: boolean;
};

const MIN_SCALE = 0.5;
const MAX_SCALE = 4;

/**
 * A CSS colour's current value as an `rgba()` string mermaid can do arithmetic
 * on. The tokens are `oklch()` and `color-mix()` expressions, which mermaid's
 * colour library does not parse, and a canvas's normalised `fillStyle` keeps a
 * wide-gamut colour in `lab()`, which it does not parse either. Painting one
 * pixel and reading it back is the one path that always yields sRGB bytes.
 */
function resolveColor(cssColor: string, fallback: string): string {
  const canvas = document.createElement("canvas");
  canvas.width = 1;
  canvas.height = 1;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (!context) return fallback;
  const probe = document.createElement("span");
  probe.style.color = cssColor;
  document.body.appendChild(probe);
  const computed = getComputedStyle(probe).color;
  probe.remove();
  context.fillStyle = computed;
  context.fillRect(0, 0, 1, 1);
  const [r, g, b, a] = context.getImageData(0, 0, 1, 1).data;
  if (a === 0) return fallback;
  return `rgba(${r}, ${g}, ${b}, ${(a / 255).toFixed(3)})`;
}

/**
 * Mermaid's `base` theme variables, filled from the surface ladder.
 *
 * The diagram is drawn INSIDE a `sunken` well, so its fills climb back up the
 * ladder from there, one rung per layer: well → cluster (`base`) → entity rows →
 * node and entity header (the top rung). Every layer then differs from the one it
 * sits on by a ladder step in both themes. `--muted` is not on the ladder and sat
 * within 0.02 L of the light page, which is why nodes, rows and the well were
 * indistinguishable there. Light has no rung above `raised`, so its top is
 * `raised`; dark's is `overlay`. The rows' zebra mixes the two rungs below the
 * top, so even rows stay apart from the header as well as from odd rows.
 *
 * @param isDark - Selects the rung that tops the stack; the tokens themselves are
 *   read live, so a retuned ladder needs no change here
 */
function themeVariables(isDark: boolean) {
  const well = resolveColor("var(--surface-sunken)", isDark ? "#000000" : "#e1e1e4");
  const cluster = resolveColor("var(--surface-base)", isDark ? "#0e0e0e" : "#efeff1");
  const raised = resolveColor("var(--surface-raised)", isDark ? "#212121" : "#ffffff");
  const node = isDark ? resolveColor("var(--surface-overlay)", "#2f2f32") : raised;
  const rowOdd = isDark ? raised : cluster;
  const rowEven = isDark
    ? resolveColor("color-mix(in oklab, var(--surface-raised) 50%, var(--surface-base))", "#181818")
    : resolveColor("color-mix(in oklab, var(--surface-base) 50%, var(--surface-sunken))", "#e8e8eb");
  const foreground = resolveColor("var(--foreground)", isDark ? "#ffffff" : "#09090b");
  const mutedForeground = resolveColor("var(--muted-foreground)", isDark ? "#b4b4bc" : "#62626c");
  const border = resolveColor("var(--surface-edge-source)", isDark ? "#3a3a40" : "#c4c4ca");
  return {
    darkMode: isDark,
    background: well,
    fontFamily: "inherit",
    primaryColor: node,
    primaryTextColor: foreground,
    primaryBorderColor: border,
    secondaryColor: node,
    tertiaryColor: well,
    mainBkg: node,
    nodeBorder: border,
    nodeTextColor: foreground,
    lineColor: mutedForeground,
    textColor: foreground,
    titleColor: foreground,
    clusterBkg: cluster,
    clusterBorder: border,
    edgeLabelBackground: well,
    rowOdd,
    rowEven,
    noteBkgColor: node,
    noteTextColor: foreground,
    noteBorderColor: border,
    actorBkg: node,
    actorBorder: border,
    actorTextColor: foreground,
    signalColor: mutedForeground,
    signalTextColor: foreground,
    labelBoxBkgColor: node,
    labelBoxBorderColor: border,
    labelTextColor: foreground,
    loopTextColor: foreground,
    activationBkgColor: cluster,
    activationBorderColor: border,
    sequenceNumberColor: well,
  };
}

/**
 * Label markup that would fetch a URL or embed a document. Mermaid's `strict`
 * level strips scripts and handlers, but it keeps `<img src>`, and a transcript
 * is prose somebody else wrote: an image in a node label is a tracking pixel
 * the reader never agreed to load.
 */
const FORBIDDEN_LABEL_TAGS = ["img", "image", "picture", "source", "video", "audio", "iframe", "object", "embed", "use"];

/**
 * Draws `code` with mermaid, themed for `isDark`.
 *
 * @param id - A DOM-safe id for the SVG; mermaid namespaces its styles by it, so two
 *   diagrams on one page must differ
 * @throws Mermaid's parse or render error, which the caller shows as the fallback
 */
async function renderWithMermaid({ id, code, isDark }: { id: string; code: string; isDark: boolean }) {
  const { default: mermaid } = await import("mermaid");
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: "strict",
    dompurifyConfig: { FORBID_TAGS: FORBIDDEN_LABEL_TAGS },
    suppressErrorRendering: true,
    theme: "base",
    themeVariables: themeVariables(isDark),
    fontFamily: "inherit",
    flowchart: { htmlLabels: false, useMaxWidth: true, curve: "basis" },
    sequence: { useMaxWidth: true },
  });
  const { svg } = await mermaid.render(id, code);
  return svg;
}

const CSS_IMPORT =
  /@import\s*(?:url\(\s*(?:"[^"]*"|'[^']*'|[^)]*)\s*\)|"[^"]*"|'[^']*')[^;]*(?:;|$)/g;

const SVG_STYLE = /<style\b[^>]*>([\s\S]*?)<\/style>/;
/** Every `<style>` block at once, for a rewrite that must touch only CSS text. */
const SVG_STYLE_BLOCKS = /<style\b[^>]*>[\s\S]*?<\/style>/g;

const splitSvgStyles = (svg: string) => {
  let css = "";
  let markup = svg;
  // Removing one <style> can splice the text around it into another, so each
  // pass takes the first block and rescans.
  for (
    let match = SVG_STYLE.exec(markup);
    match;
    match = SVG_STYLE.exec(markup)
  ) {
    css += match[1];
    markup =
      markup.slice(0, match.index) +
      markup.slice(match.index + match[0].length);
  }
  return {
    markup: markup.replace(
      /(<[^>]*?)\sstyle="([^"]*)"/g,
      '$1 data-aui-style="$2"',
    ),
    css: css.replace(CSS_IMPORT, "").trim(),
  };
};

type StyleRoot = Document | ShadowRoot;
type AdoptedSheet = { sheet: CSSStyleSheet; count: number };

const adoptedStyles = new WeakMap<StyleRoot, Map<string, AdoptedSheet>>();

const SCOPE = "[data-aui-mermaid-svg]";

const adoptStyles = (root: StyleRoot, css: string) => {
  const sheets = adoptedStyles.get(root) ?? new Map<string, AdoptedSheet>();
  adoptedStyles.set(root, sheets);
  let entry = sheets.get(css);
  if (!entry) {
    const sheet = new CSSStyleSheet();
    sheet.replaceSync(css);
    for (const rule of sheet.cssRules) {
      if (rule instanceof CSSStyleRule) {
        rule.selectorText = `:where(${SCOPE}) :is(${rule.selectorText})`;
      }
    }
    root.adoptedStyleSheets = [...root.adoptedStyleSheets, sheet];
    entry = { sheet, count: 0 };
    sheets.set(css, entry);
  }
  entry.count++;
  const adopted = entry;
  return () => {
    if (--adopted.count > 0) return;
    root.adoptedStyleSheets = root.adoptedStyleSheets.filter(
      (s) => s !== adopted.sheet,
    );
    sheets.delete(css);
  };
};

const subscribeNothing = () => () => {};

const supportsAdoptedStyles = () =>
  typeof CSSStyleSheet === "function" &&
  "replaceSync" in CSSStyleSheet.prototype &&
  "adoptedStyleSheets" in document;

type MermaidSvgProps = Omit<
  ComponentProps<"div">,
  "dangerouslySetInnerHTML"
> & {
  svg: string;
};

/**
 * Renders beautiful-mermaid's SVG without inline `<style>` or `style=""`,
 * which a Content Security Policy without 'unsafe-inline' blocks: its CSS
 * goes into a constructed stylesheet scoped to the diagram and its inline
 * styles are set through the CSSOM. Server and hydration renders, and browsers without
 * constructable stylesheets, keep the original markup.
 */
function MermaidSvg({ svg, ...props }: MermaidSvgProps) {
  const ref = useRef<HTMLDivElement>(null);
  const constructable = useSyncExternalStore(
    subscribeNothing,
    supportsAdoptedStyles,
    () => false,
  );
  const { markup, css } = useMemo(() => splitSvgStyles(svg), [svg]);

  useLayoutEffect(() => {
    const container = ref.current;
    if (!constructable || !container) return;
    for (const el of container.querySelectorAll<SVGElement>(
      "[data-aui-style]",
    )) {
      for (const declaration of (el.dataset["auiStyle"] ?? "").split(";")) {
        const colon = declaration.indexOf(":");
        if (colon <= 0) continue;
        el.style.setProperty(
          declaration.slice(0, colon).trim(),
          declaration.slice(colon + 1).trim(),
        );
      }
    }
    if (!css) return;
    const root = container.getRootNode();
    const styleRoot =
      root instanceof ShadowRoot ? root : container.ownerDocument;
    if (!("adoptedStyleSheets" in styleRoot)) return;
    return adoptStyles(styleRoot, css);
  }, [constructable, markup, css]);

  return (
    <div
      ref={ref}
      {...props}
      data-aui-mermaid-svg=""
      dangerouslySetInnerHTML={{ __html: constructable ? markup : svg }}
    />
  );
}

type MermaidZoomProps = {
  svg: string;
  children: ReactNode;
};

function MermaidZoom({ svg, children }: MermaidZoomProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [transform, setTransform] = useState({ x: 0, y: 0, scale: 1 });
  const triggerRef = useRef<HTMLButtonElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const overlayRef = useRef<HTMLDivElement>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const drag = useRef<{
    startX: number;
    startY: number;
    originX: number;
    originY: number;
  } | null>(null);
  const transformRef = useRef(transform);
  transformRef.current = transform;

  const zoomSvg = useMemo(() => {
    const rootId = /^<svg\b[^>]*\sid="([^"]+)"/.exec(svg)?.[1];
    const renamed = svg
      .replace(/id="([^"]+)"/g, 'id="$1-zoom"')
      .replace(/url\(#([^)]+)\)/g, "url(#$1-zoom)")
      .replace(/(href|xlink:href)="#([^"]+)"/g, '$1="#$2-zoom"');
    if (!rootId) return renamed;
    const rootSelector = new RegExp(`#${rootId.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?![\\w-])`, "g");
    return renamed.replace(SVG_STYLE_BLOCKS, (block) => block.replace(rootSelector, `#${rootId}-zoom`));
  }, [svg]);

  const handleClose = useCallback(() => {
    setIsOpen(false);
    setTransform({ x: 0, y: 0, scale: 1 });
    triggerRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        handleClose();
        return;
      }
      if (e.key !== "Tab") return;
      const focusables = overlayRef.current?.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])',
      );
      const first = focusables?.[0];
      const last = focusables?.[focusables.length - 1];
      if (!first || !last) return;
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, handleClose]);

  useEffect(() => {
    if (!isOpen) return;
    const originalOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = originalOverflow;
    };
  }, [isOpen]);

  useEffect(() => {
    if (isOpen) closeRef.current?.focus();
  }, [isOpen]);

  const zoomBy = useCallback((factor: number, cx?: number, cy?: number) => {
    setTransform((t) => {
      const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, t.scale * factor));
      const ratio = scale / t.scale;
      if (cx === undefined || cy === undefined) {
        const viewport = viewportRef.current;
        cx = (viewport?.clientWidth ?? 0) / 2;
        cy = (viewport?.clientHeight ?? 0) / 2;
      }
      return {
        scale,
        x: cx - (cx - t.x) * ratio,
        y: cy - (cy - t.y) * ratio,
      };
    });
  }, []);

  const onWheel = useCallback(
    (e: React.WheelEvent) => {
      const viewport = viewportRef.current;
      if (!viewport) return;
      const rect = viewport.getBoundingClientRect();
      zoomBy(
        Math.exp(-e.deltaY * 0.0015),
        e.clientX - rect.left,
        e.clientY - rect.top,
      );
    },
    [zoomBy],
  );

  const onPointerDown = useCallback((e: React.PointerEvent) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    const t = transformRef.current;
    drag.current = {
      startX: e.clientX,
      startY: e.clientY,
      originX: t.x,
      originY: t.y,
    };
  }, []);

  const onPointerMove = useCallback((e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    setTransform((t) => ({
      ...t,
      x: d.originX + e.clientX - d.startX,
      y: d.originY + e.clientY - d.startY,
    }));
  }, []);

  const onPointerUp = useCallback(() => {
    drag.current = null;
  }, []);

  return (
    <div
      data-slot="mermaid-zoom-wrap"
      className="aui-mermaid-zoom-wrap group/mermaid relative"
    >
      {children}
      <button
        ref={triggerRef}
        type="button"
        data-slot="mermaid-zoom-trigger"
        aria-label="Expand diagram"
        onClick={() => setIsOpen(true)}
        className="aui-mermaid-zoom-trigger text-muted-foreground hover:text-foreground hover:border-muted-foreground/70 border-border bg-background absolute top-2 right-2 rounded-md border p-1.5 opacity-0 transition group-hover/mermaid:opacity-100 focus-visible:opacity-100"
      >
        <Maximize2 className="size-3.5" />
      </button>
      {isOpen &&
        createPortal(
          <div
            ref={overlayRef}
            data-slot="mermaid-zoom-overlay"
            role="dialog"
            aria-modal="true"
            aria-label="Diagram"
            className="aui-mermaid-zoom-overlay fade-in animate-in bg-background fixed inset-0 z-50 duration-200"
          >
            <div
              ref={viewportRef}
              className="aui-mermaid-zoom-viewport h-full w-full cursor-grab touch-none overflow-hidden active:cursor-grabbing [background:repeating-conic-gradient(var(--surface-sunken)_0_25%,var(--surface-base)_0_50%)_0_0/24px_24px]"
              onWheel={onWheel}
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
              onPointerCancel={onPointerUp}
            >
              <MermaidSvg
                data-slot="mermaid-zoom-content"
                className="aui-mermaid-zoom-content flex h-full w-full items-center justify-center [&_svg]:max-h-[80vh] [&_svg]:max-w-[90vw]"
                style={{
                  transform: `translate(${transform.x}px, ${transform.y}px) scale(${transform.scale})`,
                  transformOrigin: "0 0",
                }}
                svg={zoomSvg}
              />
            </div>
            <div
              data-slot="mermaid-zoom-toolbar"
              className="aui-mermaid-zoom-toolbar border-border bg-background absolute top-4 right-4 flex items-center gap-1 rounded-lg border p-1"
            >
              <button
                type="button"
                aria-label="Zoom in"
                onClick={() => zoomBy(1.25)}
                className="text-muted-foreground hover:text-foreground rounded-sm p-1.5"
              >
                <Plus className="size-4" />
              </button>
              <button
                type="button"
                aria-label="Zoom out"
                onClick={() => zoomBy(0.8)}
                className="text-muted-foreground hover:text-foreground rounded-sm p-1.5"
              >
                <Minus className="size-4" />
              </button>
              <button
                type="button"
                aria-label="Reset zoom"
                onClick={() => setTransform({ x: 0, y: 0, scale: 1 })}
                className="text-muted-foreground hover:text-foreground rounded-sm p-1.5"
              >
                <RotateCcw className="size-4" />
              </button>
              <button
                ref={closeRef}
                type="button"
                aria-label="Close"
                onClick={handleClose}
                className="text-muted-foreground hover:text-foreground rounded-sm p-1.5"
              >
                <X className="size-4" />
              </button>
            </div>
          </div>,
          document.body,
        )}
    </div>
  );
}

const MermaidDiagramImpl: FC<MermaidDiagramProps> = ({
  code,
  className,
  streaming = false,
}) => {
  const { resolvedTheme } = useTheme();
  const isDark = resolvedTheme === "dark";
  const svgId = `mermaid-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const renderKey = `${isDark}\n${code}`;
  const [rendered, setRendered] = useState<
    | { key: string; svg: string; error: null }
    | { key: string; svg: null; error: Error }
    | null
  >(null);

  useEffect(() => {
    if (streaming) return;
    let cancelled = false;
    renderWithMermaid({ id: svgId, code, isDark }).then(
      (svg) => {
        if (!cancelled) setRendered({ key: renderKey, svg, error: null });
      },
      (err: unknown) => {
        if (cancelled) return;
        setRendered({ key: renderKey, svg: null, error: err instanceof Error ? err : new Error(String(err)) });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [streaming, code, isDark, svgId, renderKey]);

  // A result is shown only for the code and theme on screen now, so a diagram
  // never lags its own source while a re-render is in flight.
  const result = !streaming && rendered?.key === renderKey ? rendered : null;

  if (!result) {
    return (
      <div
        data-slot="mermaid-skeleton"
        aria-label="Rendering diagram"
        className={cn(
          "aui-mermaid-skeleton bg-surface-sunken flex h-32 animate-pulse items-center justify-center gap-3 rounded-b-lg p-4",
          className,
        )}
      >
        <div className="bg-muted-foreground/20 h-8 w-20 rounded-md" />
        <div className="bg-muted-foreground/20 h-px w-10" />
        <div className="bg-muted-foreground/20 h-8 w-20 rounded-md" />
        <div className="bg-muted-foreground/20 h-px w-10" />
        <div className="bg-muted-foreground/20 h-8 w-20 rounded-md" />
      </div>
    );
  }

  if (result.error) {
    return (
      <div
        data-slot="mermaid-fallback"
        className={cn(
          "aui-mermaid-fallback bg-surface-sunken rounded-b-lg",
          className,
        )}
      >
        <pre className="overflow-x-auto p-4 text-sm">{code.trim()}</pre>
        <p className="text-muted-foreground border-border border-t px-4 py-1.5 text-xs">
          diagram could not be rendered
          {result.error.message && (
            <span className="block truncate" title={result.error.message}>
              {result.error.message.split("\n")[0]}
            </span>
          )}
        </p>
      </div>
    );
  }

  return (
    <MermaidZoom svg={result.svg}>
      <MermaidSvg
        data-slot="mermaid-diagram"
        className={cn(
          "aui-mermaid-diagram bg-surface-sunken overflow-x-auto rounded-b-lg p-2 [&_svg]:mx-auto [&_svg]:h-auto [&_svg]:max-w-full",
          className,
        )}
        svg={result.svg}
      />
    </MermaidZoom>
  );
};

const MermaidDiagram = memo(
  MermaidDiagramImpl,
) as unknown as FC<MermaidDiagramProps> & {
  Zoom: typeof MermaidZoom;
};

MermaidDiagram.displayName = "MermaidDiagram";
MermaidDiagram.Zoom = MermaidZoom;

export { MermaidDiagram, MermaidZoom };
