"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

import { LevelHistory } from "@/lib/grove/adapters/speech";

/**
 * Bar width plus gap, in CSS pixels — the theme draws each bar inside it.
 * The reference's density: a 3px bar at a 7px pitch. A 28px pitch read as
 * "broken teeth". With a chunk every `CHUNK_MS`, a 700px strip holds 100
 * chunks ≈ 6 s of recording end to end before it starts to slide.
 */
export const WAVEFORM_BAR_PITCH = 7;

/** One bar per this much recording — a few frames, so the strip visibly flows. */
export const CHUNK_MS = 60;

/** How many bars fill a strip of this width. */
export function waveformBars(width: number): number {
  return Math.max(1, Math.floor(width / WAVEFORM_BAR_PITCH));
}

/**
 * A live level waveform: one bar per sample, newest at the right, the strip
 * sliding left as audio arrives. Pure presentation — the levels are whatever
 * the caller measured. `aria-hidden`, because the status line beside it
 * already says "Recording" and sixty bars have nothing to add to that.
 */
export function DictationWaveform({
  levels,
  className,
}: {
  readonly levels: readonly number[];
  readonly className?: string;
}): ReactNode {
  return (
    <div aria-hidden="true" data-slot="dictation-waveform" className={className}>
      {levels.map((level, index) => (
        <span
          key={index}
          data-slot="dictation-bar"
          // `--index` phases the transcribing sweep across the strip.
          style={{ ["--level" as string]: level, ["--index" as string]: index }}
        />
      ))}
    </div>
  );
}

/**
 * Records `read()` into chunks while `live`: a new chunk opens every
 * `CHUNK_MS`, and every frame folds the current level into the open one, so
 * the newest bar visibly grows as its moment gets louder. The strip holds as
 * many chunks as its MEASURED width allows and re-renders on resize, so a bar
 * that collapsed into place after the first measurement still ends up full
 * width. The history restarts on every new recording.
 */
export function useLevelHistory(
  live: boolean,
  read: () => number,
  strip: React.RefObject<HTMLElement | null>,
): readonly number[] {
  const history = useRef(new LevelHistory(1));
  const [levels, setLevels] = useState<readonly number[]>([]);

  useEffect(() => {
    const element = strip.current;
    if (!live || !element) {
      setLevels([]);
      return;
    }
    history.current = new LevelHistory(waveformBars(element.clientWidth));
    const publish = () => setLevels([...history.current.bars()]);
    const observer = new ResizeObserver(([entry]) => {
      if (!entry) return;
      history.current.resize(waveformBars(entry.contentRect.width));
      publish();
    });
    observer.observe(element);

    history.current.open();
    const opener = setInterval(() => {
      history.current.open();
      publish();
    }, CHUNK_MS);
    let frame = 0;
    const tick = () => {
      history.current.sample(read());
      publish();
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => {
      clearInterval(opener);
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [live, read, strip]);

  return levels;
}

/**
 * Tweens an element's height across a child swap, because `height: auto` →
 * `height: auto` has no transition even with `interpolate-size`. Measures the
 * box on the frame before `key` changes and animates from that height to the
 * new natural height; the Web Animations API leaves no inline style behind.
 * Honours `prefers-reduced-motion` by doing nothing.
 */
export function useHeightTween(element: React.RefObject<HTMLElement | null>, key: string): void {
  const previous = useRef<number | null>(null);
  const lastKey = useRef(key);
  useEffect(() => {
    const node = element.current;
    if (!node) return;
    if (lastKey.current === key) {
      previous.current = node.getBoundingClientRect().height;
      return;
    }
    lastKey.current = key;
    const from = previous.current;
    previous.current = node.getBoundingClientRect().height;
    if (from === null || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const to = previous.current;
    if (Math.abs(from - to) < 1) return;
    node.animate(
      [{ height: `${from}px` }, { height: `${to}px` }],
      { duration: 240, easing: "cubic-bezier(0.2, 0.8, 0.2, 1)" },
    );
  });
}
