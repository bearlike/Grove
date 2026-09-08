"use client";

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { PauseIcon, PlayIcon } from "lucide-react";

import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { cn } from "@/lib/utils";

/**
 * How Grove handles text that does not fit — TWO idioms, deliberately, because
 * two surfaces read differently.
 *
 *   `OverflowText`  a bounded pass over a line you are READING once: a fleet
 *                   card's title, its one-line status report. It reveals the
 *                   tail and comes back. One element, no clones — which is also
 *                   what lets `useOverflowMotion` drive a node inside a VENDORED
 *                   component (`StatusPill`), where injecting DOM is not an option.
 *
 *   `LoopingText`   a continuous circular marquee for a surface that is SCANNED:
 *                   `full text • full text • …` translating leftward forever at a
 *                   constant speed, wrapping by exactly one text-plus-separator
 *                   period so the seam is invisible. It only animates inside a
 *                   `ScannedTextScope`; everywhere else it is an ordinary
 *                   truncation, which is what keeps this component safe to put
 *                   inside shared atoms without redesigning every surface that
 *                   already uses them.
 *
 * THE SCOPE IS THE SEAM, NOT A PROP. `ScannedTextScope` wraps the sidebar and
 * nothing else, so "the rail loops its overflow" is one decision in one place
 * rather than a `marquee` boolean threaded through `entity.tsx`, the group
 * heading and every metric cell — each of which a future caller could set
 * differently, which is exactly the disagreement `entity.tsx` refuses an `icon`
 * prop to prevent.
 */

/** The boundary drawn between one copy of the label and the next. */
const SEPARATOR = " • ";

/**
 * Constant speed, in CSS pixels per second — never a fixed duration.
 *
 * A fixed duration makes a long branch name race and a short one crawl, so two
 * rows scrolling side by side read as two different mechanisms. Pinning the
 * speed instead means every looping label in the rail moves at the same rate and
 * the duration falls out of the period.
 */
const SCAN_SPEED = 45;

/* ------------------------------------------------------------------ *
 * Shared observers
 * ------------------------------------------------------------------ */

/**
 * ONE `ResizeObserver` and ONE `IntersectionObserver` for the whole app.
 *
 * A rail holding twenty rows now carries six looping labels each — title,
 * branch, three counters and an age — and a per-element observer pair would be
 * 240 live observers whose callbacks all fire on one rail resize. The browser
 * batches a single observer's entries into one callback, so sharing is both
 * fewer objects and fewer layout reads.
 *
 * `WeakMap`/`WeakSet` throughout: an unmounted element must not be kept alive by
 * a bookkeeping entry, and `unobserve` alone would not remove it from a plain Map.
 */
const listeners = new WeakMap<Element, () => void>();
const onScreen = new WeakSet<Element>();
let resizeObserver: ResizeObserver | undefined;
let intersectionObserver: IntersectionObserver | undefined;

function fire(target: Element): void {
  listeners.get(target)?.();
}

/**
 * Watch one element for size and viewport changes; returns its unsubscribe.
 *
 * Visibility is recorded on the element rather than passed to the callback,
 * because a resize entry has no `isIntersecting` to report and both signals
 * drive the same re-evaluation.
 */
function observe(element: Element, onChange: () => void): () => void {
  resizeObserver ??= new ResizeObserver((entries) => {
    for (const entry of entries) fire(entry.target);
  });
  intersectionObserver ??= new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (entry.isIntersecting) onScreen.add(entry.target);
      else onScreen.delete(entry.target);
      fire(entry.target);
    }
  });

  listeners.set(element, onChange);
  resizeObserver.observe(element);
  intersectionObserver.observe(element);
  return () => {
    resizeObserver?.unobserve(element);
    intersectionObserver?.unobserve(element);
    listeners.delete(element);
    onScreen.delete(element);
  };
}

/* ------------------------------------------------------------------ *
 * The scanned-text scope
 * ------------------------------------------------------------------ */

interface ScannedText {
  /** Whether overflow in this subtree loops rather than clipping. */
  readonly scanned: boolean;
  /** The reader has asked every loop in this subtree to hold still. */
  readonly paused: boolean;
  readonly setPaused: (paused: boolean) => void;
}

const ScannedTextContext = createContext<ScannedText>({
  scanned: false,
  paused: false,
  setPaused: () => {},
});

export function useScannedText(): ScannedText {
  return useContext(ScannedTextContext);
}

/**
 * The subtree whose overflowing text loops, and the one place its motion can be
 * switched off.
 *
 * The pause state lives here rather than in each label because the control has
 * to govern ALL of them at once: a reader who finds movement distracting is not
 * asking about one row. It is deliberately not persisted — `prefers-reduced-
 * motion` is the durable preference and this is the in-the-moment override.
 */
export function ScannedTextScope({
  children,
}: {
  children: React.ReactNode;
}): React.ReactNode {
  const [paused, setPaused] = useState(false);
  const value = useMemo<ScannedText>(
    () => ({ scanned: true, paused, setPaused }),
    [paused],
  );
  return (
    <ScannedTextContext.Provider value={value}>
      {children}
    </ScannedTextContext.Provider>
  );
}

/**
 * The shared pause/resume control.
 *
 * Automatic movement that a user cannot stop fails WCAG 2.2.2 outright, so this
 * is a requirement rather than a courtesy — and it has to be *discoverable*,
 * which is why it sits in the rail's own control row beside the filter menu
 * instead of behind a keyboard shortcut or a settings page. `aria-pressed`
 * carries the state; the label states the ACTION, so it reads correctly whether
 * a screen reader announces the name or the pressed state.
 */
export function MarqueePauseButton({
  className,
}: {
  className?: string;
}): React.ReactNode {
  const { paused, setPaused } = useScannedText();
  const label = paused ? "Resume scrolling text" : "Pause scrolling text";
  return (
    <TooltipIconButton
      variant="outline"
      tooltip={label}
      aria-label={label}
      aria-pressed={paused}
      className={cn("bg-transparent dark:bg-transparent border-edge-control size-7", className)}
      data-testid="marquee-pause"
      onClick={() => setPaused(!paused)}
    >
      {paused ? <PlayIcon /> : <PauseIcon />}
    </TooltipIconButton>
  );
}

/* ------------------------------------------------------------------ *
 * The circular marquee
 * ------------------------------------------------------------------ */

/**
 * One label, looping leftward while it overflows and is worth animating.
 *
 * WHY TWO COPIES AND NOT A SCROLL. Translating a track that holds
 * `text • text •` by exactly one period (`text` plus one separator) puts the
 * second copy precisely where the first began, so the loop restarts with no
 * visible jump and no pause at either end. Two copies are provably enough:
 * the period exceeds the viewport whenever the text overflows it, so the track
 * (two periods) always covers viewport-plus-offset.
 *
 * WHY THE CLONE IS ADDED LATE. It renders only once measurement says the label
 * overflows, which keeps the server-rendered markup identical to the first
 * client render — the same hydration rule `RelativeTime` follows — and leaves a
 * fitting label as one plain span with no marquee machinery around it.
 *
 * WHAT NEVER MOVES: the viewport, so the row's icons, background and click
 * target are stationary. Only the inner track is animated, on `transform`, so
 * the work stays off the main thread and off the layout path.
 *
 * WHEN IT DOES NOT ANIMATE — each of these is a requirement, not a fallback:
 * outside a `ScannedTextScope`; when the text fits; under
 * `prefers-reduced-motion`; while the row is scrolled out of view; while the
 * document is hidden (NOT merely blurred — an unfocused window is still being
 * read); and while the shared pause control is engaged.
 */
export function LoopingText({
  children,
  className,
}: {
  children: string;
  className?: string;
}): React.ReactNode {
  const { scanned, paused } = useScannedText();
  const viewport = useRef<HTMLSpanElement>(null);
  const track = useRef<HTMLSpanElement>(null);
  const copy = useRef<HTMLSpanElement>(null);
  const clone = useRef<HTMLSpanElement>(null);
  const animation = useRef<Animation | null>(null);

  const [looping, setLooping] = useState(false);
  /** In view AND the document is visible — the two gates that are not preferences. */
  const [live, setLive] = useState(false);
  /**
   * The last measured width of one copy. Not rendered: it exists so a font
   * swap or a text change re-derives the PERIOD, which no other dependency
   * would notice — the period is a property of the text, not of the container,
   * so a resize alone must not restart the animation.
   */
  const [measured, setMeasured] = useState(0);

  useEffect(() => {
    const box = viewport.current;
    const first = copy.current;
    if (!box || !first) return;
    if (!scanned) {
      setLooping(false);
      setLive(false);
      return;
    }

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    const sync = (): void => {
      const width = first.scrollWidth;
      setMeasured(width);
      setLooping(
        !reduced.matches && box.clientWidth > 0 && width - box.clientWidth > 1,
      );
      setLive(!document.hidden && onScreen.has(box));
    };

    const stop = observe(box, sync);
    sync();
    reduced.addEventListener("change", sync);
    document.addEventListener("visibilitychange", sync);
    // A web font that lands after first paint changes every measurement here.
    void document.fonts?.ready.then(sync).catch(() => {});

    return () => {
      stop();
      reduced.removeEventListener("change", sync);
      document.removeEventListener("visibilitychange", sync);
    };
  }, [scanned, children]);

  // Build the animation. Separate from the gate below so pausing RESUMES where
  // it stopped instead of snapping the label back to its first character.
  useEffect(() => {
    const rail = track.current;
    const first = copy.current;
    const second = clone.current;
    if (!looping || !rail || !first || !second) return;

    // The distance between the two copies' left edges IS the period — one copy
    // plus one separator — measured rather than assembled from two widths, so a
    // separator whose glyph metrics differ per font can never desynchronise it.
    const period = second.offsetLeft - first.offsetLeft;
    if (period <= 0) return;

    const running = rail.animate(
      [{ transform: "translateX(0px)" }, { transform: `translateX(-${period}px)` }],
      {
        duration: (period / SCAN_SPEED) * 1000,
        iterations: Infinity,
        easing: "linear",
      },
    );
    running.pause();
    animation.current = running;
    return () => {
      running.cancel();
      animation.current = null;
    };
  }, [looping, measured]);

  useEffect(() => {
    const running = animation.current;
    if (!running) return;
    if (paused || !live) running.pause();
    else running.play();
  }, [paused, live, looping, measured]);

  return (
    <span
      ref={viewport}
      data-testid="looping-text"
      data-looping={looping ? "true" : "false"}
      className={cn("block min-w-0 overflow-hidden", className)}
    >
      <span ref={track} className={cn("flex", looping ? "w-max" : "w-full")}>
        <span
          ref={copy}
          className={cn(
            "whitespace-nowrap",
            !looping && "min-w-0 flex-1 truncate",
          )}
        >
          {children}
        </span>
        {looping ? (
          // Every clone is `aria-hidden`: the label is announced once, by the
          // copy above, and a screen reader must never hear the branch name
          // three times because the sighted reading needed three copies.
          <>
            <span aria-hidden className="whitespace-pre">
              {SEPARATOR}
            </span>
            <span ref={clone} aria-hidden className="whitespace-nowrap">
              {children}
            </span>
            <span aria-hidden className="whitespace-pre">
              {SEPARATOR}
            </span>
          </>
        ) : null}
      </span>
    </span>
  );
}

/* ------------------------------------------------------------------ *
 * The bounded pass
 * ------------------------------------------------------------------ */

/** Overflow earns motion, not another tooltip-only identity. Offscreen rows stay still. */
export function useOverflowMotion(selector?: string, content?: string) {
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    const root = ref.current;
    const label = selector ? root?.querySelector<HTMLElement>(selector) : root;
    if (!label) return;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    let animation: Animation | undefined;
    let visible = false;
    const update = () => {
      animation?.cancel();
      animation = undefined;
      const overflow = label.scrollWidth - label.clientWidth;
      if (!visible || reduced.matches || document.hidden || label.clientWidth === 0 || overflow <= 1) return;
      animation = label.animate([
        { textIndent: "0px", textOverflow: "clip", offset: 0 },
        { textIndent: "0px", textOverflow: "clip", offset: 0.2 },
        { textIndent: `-${overflow}px`, textOverflow: "clip", offset: 0.8 },
        { textIndent: `-${overflow}px`, textOverflow: "clip", offset: 1 },
      ], { duration: overflow * 40 + 3000, iterations: Infinity, direction: "alternate" });
    };
    const resize = new ResizeObserver(update);
    const intersection = new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; update(); });
    resize.observe(label);
    intersection.observe(label);
    reduced.addEventListener("change", update);
    document.addEventListener("visibilitychange", update);
    return () => {
      resize.disconnect(); intersection.disconnect(); animation?.cancel();
      reduced.removeEventListener("change", update);
      document.removeEventListener("visibilitychange", update);
    };
  }, [selector, content]);
  return ref;
}

export function OverflowText({ children, className }: { children: string; className?: string }): React.ReactNode {
  const ref = useOverflowMotion(undefined, children);
  return <span ref={ref} title={children} className={cn("block min-w-0 truncate", className)}>{children}</span>;
}
