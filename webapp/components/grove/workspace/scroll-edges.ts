"use client";

import { useEffect, useRef, useState, type RefObject } from "react";

export interface ScrollEdges {
  /** True while the scroller is at its start — nothing is hidden above. */
  atTop: boolean;
  /** True while the scroller is at its end — nothing is hidden below. */
  atBottom: boolean;
  /** Attach to a zero-height element at the very start of the scrolled content. */
  topRef: RefObject<HTMLDivElement | null>;
  /** Attach to a zero-height element at the very end of the scrolled content. */
  bottomRef: RefObject<HTMLDivElement | null>;
}

/**
 * Whether a scroll container is at its edges, for a conditional edge cue.
 *
 * WHY SENTINELS AND AN OBSERVER, NOT A SCROLL LISTENER. Answering "am I at the
 * top" from a scroll event means reading `scrollTop`/`scrollHeight`/
 * `clientHeight`, and those are layout reads — on a transcript whose scroll
 * height runs to six figures, doing that on every frame of a flick is exactly
 * the cost this app has already paid once. An `IntersectionObserver` fires only
 * when a sentinel actually crosses the root's boundary: no reads, no polling,
 * and nothing at all while the reader sits still.
 *
 * The sentinels are zero-height markers in the content rather than measurements
 * of it, so this stays correct while the transcript grows underneath — a new
 * message moves the bottom sentinel and the observer re-fires on its own.
 *
 * Both default TRUE so the first paint carries no scrim: a transcript that has
 * not been measured yet is far more likely to be at rest than scrolled, and a
 * cue that flashes on load is worse than one that arrives a frame late.
 */
export function useScrollEdges(rootRef: RefObject<HTMLElement | null>): ScrollEdges {
  const topRef = useRef<HTMLDivElement | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const [atTop, setAtTop] = useState(true);
  const [atBottom, setAtBottom] = useState(true);

  useEffect(() => {
    const root = rootRef.current;
    const top = topRef.current;
    const bottom = bottomRef.current;
    if (!root || !top || !bottom) return;

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.target === top) setAtTop(entry.isIntersecting);
          else if (entry.target === bottom) setAtBottom(entry.isIntersecting);
        }
      },
      { root },
    );
    observer.observe(top);
    observer.observe(bottom);
    return () => observer.disconnect();
  }, [rootRef]);

  return { atTop, atBottom, topRef, bottomRef };
}
