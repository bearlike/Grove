"use client";

import { create } from "zustand";

import type { OnboardingStep } from "./steps";

/** Where "this person has seen the tour" is remembered, beside the rail's collapse key. */
export const SEEN_KEY = "grove.onboarding.seen";

/**
 * What a tour step asks the PAGE to do on its behalf.
 *
 * The tour lives in the shell and the pages' state lives in the pages, with no
 * common parent below the shell — the same reason `useAnnotationUi` is a
 * store. A step that wants a sample image staged, a brief written, or a work
 * tab selected posts a demand here; the page that owns that state consumes it
 * and clears it. One field, one subscriber per page. A demand posted before
 * its page has mounted waits for it: that is what lets a step navigate and ask
 * in one move.
 */
export type OnboardingDemand =
  // landing page
  | { readonly kind: "sample-image" }
  | { readonly kind: "annotate" }
  | { readonly kind: "prompt"; readonly text: string }
  | { readonly kind: "reset" }
  // workspace page
  | { readonly kind: "pane"; readonly view: "split" | "transcript" | "work" }
  | { readonly kind: "work-tab"; readonly tab: "terminal" | "info" | "diagram" }
  | { readonly kind: "workspace-prompt"; readonly text: string };

interface OnboardingUi {
  readonly open: boolean;
  readonly demand: OnboardingDemand | null;
  /** The built step list for the current run, published by the bridge for the card to read. */
  readonly steps: readonly OnboardingStep[];
  setOpen(open: boolean): void;
  setSteps(steps: readonly OnboardingStep[]): void;
  demandFor(demand: OnboardingDemand): void;
  /** Take the demand if it is one of `kinds`; returns null when it belongs to another page. */
  take<K extends OnboardingDemand["kind"]>(
    kinds: readonly K[],
  ): Extract<OnboardingDemand, { kind: K }> | null;
}

export const useOnboardingUi = create<OnboardingUi>((set, get) => ({
  open: false,
  demand: null,
  steps: [],
  setOpen: (open) => set(open ? { open } : { open, demand: null }),
  setSteps: (steps) => set({ steps }),
  demandFor: (demand) => set({ demand }),
  take: (kinds) => {
    const { demand } = get();
    if (!demand || !(kinds as readonly string[]).includes(demand.kind)) return null;
    set({ demand: null });
    return demand as never;
  },
}));

/** Remember that the tour was shown, so a first visit opens it exactly once. */
export function markSeen(): void {
  if (typeof window !== "undefined") window.localStorage.setItem(SEEN_KEY, "true");
}

export function hasSeen(): boolean {
  return typeof window !== "undefined" && window.localStorage.getItem(SEEN_KEY) === "true";
}
