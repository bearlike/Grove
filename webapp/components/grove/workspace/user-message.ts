"use client";

import { useEffect, useRef, useState, type CSSProperties, type RefObject } from "react";

/**
 * The collapse decision for a long user prompt — measured, never guessed.
 *
 * Lives apart from the rendering so the rule can be exercised without a layout
 * engine: jsdom reports `scrollHeight === 0`, so a test that mounted the bubble
 * could only ever pin the no-overflow arm.
 */

/** Lines of a user bubble shown before the fade. */
export const USER_MESSAGE_CLAMP_LINES = 6;

/**
 * The clamp height, derived from the line count in ONE place.
 *
 * The bubble renders at `text-base` with the default 1.5 leading (24px), so the
 * cap is lines × 1.5rem. Deriving it here rather than writing a Tailwind
 * `max-h-*` class is what stops the class and the constant drifting apart.
 */
export const USER_MESSAGE_CLAMP_HEIGHT = `${USER_MESSAGE_CLAMP_LINES * 1.5}rem`;

/**
 * Fade the clipped tail rather than cutting it.
 *
 * A mask, not a gradient overlay: the full text stays in the DOM (so copy and
 * find-in-page still see it) and no colour is introduced, which a background
 * gradient would need in order to match whatever sits behind the bubble.
 */
const FADE = "linear-gradient(to bottom, black 60%, transparent 100%)";

/** The inline style for the clamped bubble, or `undefined` when it is expanded.
 * The fade applies only when text is genuinely hidden — a two-line prompt must
 * not wear a gradient over its last line. */
export function clampStyle(collapsed: boolean, overflowing: boolean): CSSProperties | undefined {
  if (!collapsed) return undefined;
  const clipped: CSSProperties = { maxHeight: USER_MESSAGE_CLAMP_HEIGHT, overflow: "hidden" };
  return overflowing ? { ...clipped, maskImage: FADE, WebkitMaskImage: FADE } : clipped;
}

/**
 * Whether to offer the toggle.
 *
 * `expanded` alone qualifies because expanding removes the clamp, so the
 * element stops overflowing and the control that got you here would otherwise
 * vanish under you.
 */
export function showsToggle(overflowing: boolean, expanded: boolean): boolean {
  return overflowing || expanded;
}

export interface UserMessageCollapse {
  ref: RefObject<HTMLDivElement | null>;
  expanded: boolean;
  toggle: () => void;
  /** The bubble is taller than the clamp, so collapsing it hides something. */
  overflowing: boolean;
}

/**
 * One `ResizeObserver` shared by every user-message bubble on the transcript.
 *
 * A per-bubble observer measured at 134 instances on a single transcript
 * mount — one construction, one native callback registration, per message,
 * for a signal (own-element resize) that a single observer already delivers
 * via `entry.target`. `observe` fans a shared callback out to whichever
 * element resized; disposing an entry only removes its callback and calls
 * `unobserve` on that element, it never tears the shared observer down —
 * a transcript remounts constantly, and churning the observer itself is
 * exactly the construction cost this exists to remove.
 */
export class UserMessageResizeObserverRegistry {
  private observer: ResizeObserver | null = null;
  private readonly callbacks = new Map<Element, () => void>();

  observe(element: Element, callback: () => void): () => void {
    if (typeof ResizeObserver === "undefined") {
      // SSR / this repo's jsdom-less unit environment: no layout engine to
      // observe, so the caller's immediate measurement is all it gets.
      return () => {};
    }
    this.observer ??= new ResizeObserver((entries) => {
      for (const entry of entries) {
        this.callbacks.get(entry.target)?.();
      }
    });
    this.callbacks.set(element, callback);
    this.observer.observe(element);
    return () => {
      this.callbacks.delete(element);
      this.observer?.unobserve(element);
    };
  }
}

const sharedResizeObserver = new UserMessageResizeObserverRegistry();

/**
 * Track whether a bubble overflows its clamp.
 *
 * A `ResizeObserver` rather than a one-shot measurement because the same text
 * overflows at one pane width and not at another, and dragging the split
 * re-wraps it live. The observer itself is shared module-wide — see
 * `UserMessageResizeObserverRegistry`.
 */
export function useUserMessageCollapse(): UserMessageCollapse {
  const ref = useRef<HTMLDivElement | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [overflowing, setOverflowing] = useState(false);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const measure = (): void => setOverflowing(element.scrollHeight > element.clientHeight);
    measure();
    return sharedResizeObserver.observe(element, measure);
  }, []);

  return {
    ref,
    expanded,
    overflowing,
    toggle: () => setExpanded((value) => !value),
  };
}
