"use client";

import { useEffect, useState } from "react";

import { GenerationLoader } from "@/components/elements/loading-state";
import { cn } from "@/lib/utils";

/**
 * How long the matrix holds each position, in milliseconds.
 *
 * This is the STEP, not a raw tick. The vendored loader derives its position as
 * `Math.floor(tick / 3)`, so two of every three ticks re-rendered nine spans and
 * changed nothing on screen; feeding it `step * 3` means one render per visible
 * change and makes this constant mean what it says. Measured after: 267ms per
 * step against 600ms before, at the same 8% of frames mid-fade.
 *
 * 225ms is deliberately INSIDE the cells' own 300ms cross-fade, and that is
 * fine: `opacity` retargets from wherever it currently is, so an interrupted
 * fade shortens rather than tearing. The earlier worry that a sub-300ms step
 * would smear the matrix into grey was wrong — measured, cells still spend ~80%
 * of frames at a settled opacity.
 */
const STEP_MS = 225;

/**
 * ONE animation clock for every loader on the page, for the reason
 * `relative-time.tsx`'s `useNow` is shared: a rail listing ten working
 * workspaces would otherwise stand up ten intervals, and — worse — they would
 * drift out of phase, so ten marks that are obviously the same mark would each
 * be showing a different frame.
 *
 * This is still NOT that clock. `useNow` exists so two *durations* are computed
 * at the same instant and ticks once a minute; this is a frame counter carrying
 * no time, which nothing outside this file may read.
 *
 * The interval exists only while something is subscribed AND the animation is
 * wanted, so an idle fleet costs nothing and a hidden tab costs nothing.
 */
const subscribers = new Set<(tick: number) => void>();
let timer: ReturnType<typeof setInterval> | undefined;
let step = 0;
let listening = false;

/**
 * The reduced-motion query, created ONCE.
 *
 * `window.matchMedia` returns a NEW object for every call — measured in the
 * browser, two calls with the identical string are `!==` — so a query created
 * per subscriber can never remove its own listener: the cleanup asks a
 * different object to forget a listener it was never given. One query per
 * process is also what makes the add/remove below symmetric.
 */
let motionQuery: MediaQueryList | undefined;

function motion(): MediaQueryList {
  return (motionQuery ??= window.matchMedia("(prefers-reduced-motion: reduce)"));
}

/** Reduced motion and a hidden document each stand the animation down. Hidden,
 * not blurred: an unfocused Grove is still being read — the same distinction
 * `overflow-text.tsx` draws for the rail's marquee. */
function wanted(): boolean {
  return !motion().matches && !document.hidden;
}

function sync(): void {
  const live = subscribers.size > 0;

  // THE LISTENERS BELONG TO THE CLOCK AND LAST AS LONG AS ANYTHING IS
  // SUBSCRIBED — never one registration per subscriber. `addEventListener`
  // dedupes by function reference, so N loaders registering this same `sync`
  // is ONE entry in the registry, and the FIRST unmount's `removeEventListener`
  // took it away from every loader still on screen. The clock then never heard
  // "visible" again: come back to the tab and every matrix held its last frame,
  // in the rail and the transcript alike, until a reload. Reproduced on the
  // running app — four loaders mounted, tab visible, one frame across 2s.
  //
  // Gate on the FLAG rather than on `wanted()`: a hidden tab stops the timer
  // but must keep listening, or nothing is left to notice it coming back.
  if (live && !listening) {
    motion().addEventListener("change", sync);
    document.addEventListener("visibilitychange", sync);
    listening = true;
  } else if (!live && listening) {
    motion().removeEventListener("change", sync);
    document.removeEventListener("visibilitychange", sync);
    listening = false;
  }

  const run = live && wanted();
  if (run && timer === undefined) {
    timer = setInterval(() => {
      step += 1;
      // `* 3` undoes the vendored `Math.floor(tick / 3)`, so one interval fire
      // is exactly one visible position change.
      for (const notify of subscribers) notify(step * 3);
    }, STEP_MS);
  } else if (!run && timer !== undefined) {
    clearInterval(timer);
    timer = undefined;
  }
}

function useWorkingTick(): number {
  const [tick, setTick] = useState(0);

  // Subscribe and unsubscribe, nothing else: `sync` owns the timer AND the
  // listeners, so membership is the only thing a mounting loader has to say.
  useEffect(() => {
    subscribers.add(setTick);
    sync();

    return () => {
      subscribers.delete(setTick);
      sync();
    };
  }, []);

  return tick;
}

/**
 * The agent is working — shown in the transcript while nothing else says so.
 *
 * WHY THE TICK IS A PROP UPSTREAM: the vendored loader is a pure function of
 * `tick`, so the caller owns the clock. That is what lets this suspend
 * completely instead of animating into an empty room, which a self-driving CSS
 * animation could not do. Mounting is the gate that matters most — this renders
 * only while the agent actually works.
 *
 * Its SIZE is governed in `globals.css`, not here: the cells are sized off the
 * same ramp step the label reads, so the two stay proportional on every
 * surface. See that rule for why a `size-*` utility here — or an `em` — would
 * be the wrong repair.
 */
export function WorkingLoader() {
  return (
    <GenerationLoader
      // `rounded` over the default `dots`: the transcript's own marks — tool
      // call chips, badges, the composer — are all soft rectangles, and a grid
      // of circles beside them reads as a loading spinner from another app.
      variant="rounded"
      label="Working"
      tick={useWorkingTick()}
      // Left, with the footer's siblings, rather than the component's own
      // centred default: it sits in a column with the sending echo, the plan
      // and the queue, and a centred item in that stack reads as unrelated to
      // the ones above it. `gap-1.5` replaces the vendored `gap-4`, which was
      // spaced for a full-page loader rather than a line in a stack.
      className="items-start gap-1.5"
      // The same courtesy-confirmation contract `SendingEcho` uses: worth
      // announcing once, never worth interrupting what a reader is already
      // hearing. The label never changes, so it announces once and stays quiet
      // while the matrix moves.
      role="status"
      data-testid="working-loader"
    />
  );
}

/**
 * The same mark, without its word, for a row in the rail.
 *
 * A SEPARATE COMPOSITION RATHER THAN A `labelled` PROP — the design system's §3
 * rule, the one `relative-time.tsx` follows with four functions instead of a
 * `precision` knob. The two differ in what they ARE, not in a setting: one is a
 * line in a stack that has room to say "Working", the other is a mark in a
 * 28px title band whose every other pixel is the workspace's name.
 *
 * The empty `label` is what suppresses the word — the vendored component always
 * renders its label element, and hiding that element from the outside would
 * mean styling a vendored internal. The name is not lost: it moves to
 * `aria-label` on the wrapper, so a screen reader still hears "Working" and the
 * title band keeps its pixels. `gap-0` because an empty flex item still takes
 * the gap before it.
 */
export function WorkingMark({ className }: { className?: string }) {
  return (
    <GenerationLoader
      variant="rounded"
      label=""
      tick={useWorkingTick()}
      className={cn("gap-0", className)}
      role="status"
      aria-label="Working"
      data-testid="working-mark"
    />
  );
}
