"use client";

import { useEffect, useMemo, useRef } from "react";
import { usePathname, useRouter } from "next/navigation";
import { XIcon } from "lucide-react";
import { TourProvider, useTour, type PopoverContentProps, type StepType } from "@reactour/tour";

import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/kbd";
import { useQueryClient } from "@tanstack/react-query";

import type { DashboardSnapshotView } from "@/lib/grove/api";
import { groveKeys } from "@/lib/grove/hooks/keys";
import { useSidebarUi } from "@/components/grove/shell/sidebar-state";
import { installDemoInterceptor } from "./demo-interceptor";
import { DEMO_WORKSPACE_ID, withDemoWorkspace, withoutDemoWorkspace } from "./demo-workspace";
import { hasSeen, markSeen, useOnboardingUi } from "./onboarding-store";
import { buildSteps, type OnboardingStep, type TourContext } from "./steps";

/**
 * The guided tour, mounted ONCE in the shell.
 *
 * WHY reactour and not a composition of the vendored `Popover`: a tour is a
 * mask with a hole cut over a live element plus a popover positioned against
 * that hole, tracking it through resize and mutation. Radix's popover anchors
 * to a trigger it owns and knows nothing about masks; reactour is ~100 KB
 * unpacked and does exactly this job. It is styled entirely at the theme
 * boundary (`.reactour__popover` / `.reactour__mask` in `globals.css`) and its
 * content is rendered by `TourCard` below, so nothing in this tree names a
 * colour, a radius or a shadow — the same rule every other Grove component
 * lives under.
 *
 * The tour spans TWO routes — the landing composer and one workspace — and the
 * bridge below navigates between them as the steps demand. The store owns
 * `open` because the account menu (rail) and the welcome trigger (page) share
 * no parent below the shell — the same reason the annotation pane's request is
 * a store.
 */
export function OnboardingTour({ children }: { children: React.ReactNode }) {
  return (
    <TourProvider
      steps={[]}
      ContentComponent={TourCard}
      showBadge={false}
      showCloseButton={false}
      showNavigation={false}
      padding={{ mask: 6, popover: 12 }}
      // GEOMETRY only, never a colour: the mask's cut-out is an SVG rect whose
      // corner reactour sets from this value alone (a stylesheet cannot reach
      // an SVG attribute), so the container radius is restated here as a
      // number. Every colour, edge and shadow is `globals.css`'s.
      styles={{ maskArea: (base) => ({ ...base, rx: 6 }) }}
      // The click-through rect over the hole is the one element sitting exactly
      // on the anchor, so it carries the glow; the class is styled at the theme
      // boundary (`.tour-highlight` in `globals.css`), never here.
      highlightedMaskClassName="tour-highlight"
      // The mask is a scrim, never a click target: dismissing by accident on
      // the first stray click is how a tour gets abandoned at step two.
      onClickMask={() => undefined}
      disableInteraction
      // A step that has just navigated has no anchor for a frame or two;
      // drawing the mask and popover against an empty rectangle is the
      // "popover at the page origin" failure. Nothing renders until the
      // anchor exists, and the refresh below re-measures the moment it does.
      // `TourProvider` spreads every prop onto `Tour`, which reads this one;
      // only the provider's TYPE omits it, hence the cast.
      {...({ disableWhenSelectorFalsy: true } as object)}
      // Every anchor sits under the vendored `TooltipProvider`; locking focus
      // inside the popover would trap keyboard users away from the composer.
      disableFocusLock
      scrollSmooth
      accessibilityOptions={{
        closeButtonAriaLabel: "Close tour",
        showNavigationScreenReaders: true,
      }}
    >
      <TourBridge />
      {children}
    </TourProvider>
  );
}

/** reactour's shape of our steps: one popover per stop, positioned as the step asks. */
function toReactour(steps: readonly OnboardingStep[]): StepType[] {
  return steps.map((step) => ({
    selector: step.selector,
    position: step.position,
    content: step.title,
  }));
}

/** `/w/<id>` → id, else null. */
function workspaceIdOf(pathname: string): string | null {
  const match = /^\/w\/([^/]+)/.exec(pathname);
  return match?.[1] ?? null;
}

/**
 * Wires the store's `open` to reactour's, and does the three things the tour
 * cannot do for itself: serve the demo workspace while it is open, put the
 * page each step needs on screen (route, expanded rail), and hand each step's
 * demand to the page before the step is shown.
 */
function TourBridge(): null {
  const { isOpen, setIsOpen, currentStep, setCurrentStep, setSteps } = useTour();
  const open = useOnboardingUi((state) => state.open);
  const setOpen = useOnboardingUi((state) => state.setOpen);
  const demandFor = useOnboardingUi((state) => state.demandFor);
  const setCollapsed = useSidebarUi((state) => state.setCollapsed);
  const pathname = usePathname();
  const router = useRouter();
  const queryClient = useQueryClient();

  // THE TOUR WALKS ONE FICTIONAL WORKSPACE, NEVER A REAL ONE. Picking "the most
  // recent workspace" showed every reader a different page — an empty Info
  // tab, no tickets, whatever the terminal happened to be doing — and the step
  // text could only hedge. `demo-workspace.ts` is a fully populated workspace
  // with an open diagram, and `demo-interceptor.ts` serves it from the browser
  // for exactly as long as the tour is open; nothing reaches the daemon.
  const context = useMemo<TourContext>(
    () => ({ workspaceId: DEMO_WORKSPACE_ID, hasDiagram: true }),
    [],
  );
  const steps = useMemo(() => buildSteps(context), [context]);
  const step: OnboardingStep | undefined = steps[currentStep];

  // reactour keeps `steps` in its own state, seeded once from the prop, so a
  // changed list has to be pushed in. `setSteps` is not stable across renders
  // in reactour's own typing, so it is deliberately left out of the deps.
  const publishSteps = useOnboardingUi((state) => state.setSteps);
  useEffect(() => {
    setSteps?.(toReactour(steps));
    publishSteps(steps);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [steps, publishSteps]);

  // Store → reactour. Opening installs the demo, expands the rail so its
  // anchors have a rectangle, and starts from the first step. The demo is
  // uninstalled on close and the caches it answered are dropped, so a reader
  // who stays on the demo route after the tour sees an honest 404 rather than
  // a workspace the daemon has never heard of.
  const uninstall = useRef<(() => void) | null>(null);
  useEffect(() => {
    if (open && !isOpen) {
      uninstall.current ??= installDemoInterceptor();
      // THE SNAPSHOT IS WRITTEN INTO THE CACHE, NOT INVALIDATED. The daemon
      // sends a full `snapshot` frame exactly once, on connect, then deltas
      // only; and while the stream is healthy the activity query's interval
      // is off, so an invalidate here is a no-op. The interceptor's splice
      // covers a FUTURE connect and the fallback poll; this write is what
      // makes the demo exist right now — and without it `primarySessionId`
      // is null and the transcript shows "Pick a session to follow" until
      // some unrelated reconnect happens to deliver a snapshot.
      queryClient.setQueryData<DashboardSnapshotView>(groveKeys.activity, (current) =>
        current ? withDemoWorkspace(current) : current,
      );
      setCollapsed(false);
      setCurrentStep(0);
      markSeen();
      setIsOpen(true);
    }
    if (!open && isOpen) setIsOpen(false);
  }, [open, isOpen, queryClient, setCollapsed, setCurrentStep, setIsOpen]);

  // reactour → store, so Escape and the close control clear the store too.
  // Only a close reactour PERFORMED is mirrored: on the render that opens the
  // store, `isOpen` is still false and mirroring that would close the tour
  // before it opened.
  const wasOpen = useRef(false);
  useEffect(() => {
    if (isOpen) wasOpen.current = true;
    else if (wasOpen.current) {
      wasOpen.current = false;
      uninstall.current?.();
      uninstall.current = null;
      if (workspaceIdOf(window.location.pathname) === DEMO_WORKSPACE_ID) router.push("/");
      queryClient.removeQueries({ queryKey: groveKeys.workspace(DEMO_WORKSPACE_ID) });
      queryClient.setQueryData<DashboardSnapshotView>(groveKeys.activity, (current) =>
        current ? withoutDemoWorkspace(current) : current,
      );
      setOpen(false);
    }
  }, [isOpen, queryClient, router, setOpen]);

  // Each step puts its page on screen, then posts its demand. The demand
  // waits in the store until the page that owns it mounts and takes it, so
  // "navigate and select a tab" is one step rather than two.
  useEffect(() => {
    if (!isOpen || !step) return;
    const want =
      step.route === "landing" ? "/" : step.route === "workspace" ? `/w/${context.workspaceId}` : null;
    if (want && pathname !== want) router.push(want);
    if (step.demand) demandFor(step.demand);
  }, [context.workspaceId, demandFor, isOpen, pathname, router, step]);

  // KEEP THE GEOMETRY HONEST. reactour measures its anchor on mount and on a
  // window `resize`, and on nothing else — so a chip staged by a demand, a
  // pane opening beside the page or a route that finished rendering after the
  // step began all left the hole and the popover where the anchor USED to be,
  // until the reader happened to resize (or return to the tab, which refetches
  // and remounts). A `resize` event is the one refresh input it exposes, so a
  // MutationObserver over the document and a ResizeObserver on the anchor's
  // ancestors both funnel into one, coalesced per frame. Cheap: it only runs
  // while the tour is open.
  //
  // THE DISPATCH IS GATED ON THE ANCHOR'S RECT ACTUALLY CHANGING. reactour's
  // popover answers every `resize` by re-measuring itself into fresh state,
  // which restyles its own element, which a mutation observer sees — so an
  // ungated observer→resize→restyle→observer cycle pegs the main thread.
  // Comparing the anchor's rectangle (and its presence) to the last one
  // dispatched is what breaks the cycle: reactour's own restyling moves the
  // anchor by nothing.
  useEffect(() => {
    if (!isOpen || !step) return;
    let frame = 0;
    let last = "";
    const refresh = () => {
      if (frame) return;
      frame = window.requestAnimationFrame(() => {
        frame = 0;
        const anchor = document.querySelector(step.selector);
        const rect = anchor?.getBoundingClientRect();
        const key = rect
          ? `${Math.round(rect.x)},${Math.round(rect.y)},${Math.round(rect.width)},${Math.round(rect.height)}`
          : "none";
        if (key === last) return;
        last = key;
        window.dispatchEvent(new Event("resize"));
      });
    };
    const mutations = new MutationObserver(refresh);
    mutations.observe(document.body, { childList: true, subtree: true });
    // Smooth scrolling, pane transitions and images decoding all move the
    // anchor without adding a node; a slow poll catches what mutations miss.
    const settle = window.setInterval(refresh, 200);
    refresh();
    return () => {
      mutations.disconnect();
      window.clearInterval(settle);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, [isOpen, step]);

  // A first visit opens the tour once, after the landing page has painted. The
  // check runs in an effect, never at render: `localStorage` does not exist on
  // the server and reading it during render is a hydration mismatch.
  useEffect(() => {
    if (pathname !== "/" || hasSeen()) return;
    const timer = window.setTimeout(() => setOpen(true), 600);
    return () => window.clearTimeout(timer);
  }, [pathname, setOpen]);

  return null;
}

/**
 * The popover's body: title, two sentences, a step counter and Back / Next.
 * reactour renders this inside its own positioned div; the surface, edge and
 * shadow of that div come from the theme so this file composes type and
 * buttons only. The Grove step list is read from the store's context rather
 * than reactour's `steps`, which carry only the title.
 */
function TourCard({ currentStep, setCurrentStep, setIsOpen, steps }: PopoverContentProps) {
  const last = currentStep === steps.length - 1;
  const step = useTourStep(currentStep);
  if (!step) return null;

  return (
    <div className="flex w-72 flex-col gap-3" data-testid="onboarding-card" data-step={currentStep}>
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 flex-col gap-1">
          <span className="text-xs text-content-tertiary tabular-nums">
            {currentStep + 1} of {steps.length}
          </span>
          <h2 className="text-base font-medium text-content-primary" id="onboarding-title">
            {step.title}
          </h2>
        </div>
        <TooltipIconButton
          tooltip="Close tour"
          side="left"
          className="min-h-[24px] min-w-[24px] shrink-0"
          onClick={() => setIsOpen(false)}
          data-testid="onboarding-close"
        >
          <XIcon className="size-3.5" />
        </TooltipIconButton>
      </div>
      <p className="text-sm text-content-secondary">{step.body}</p>
      <div className="flex items-center justify-between gap-2">
        <span className="hidden items-center gap-1 whitespace-nowrap text-xs text-content-tertiary sm:flex">
          <Kbd>←</Kbd>
          <Kbd>→</Kbd>
          <span>move</span>
          <Kbd className="ml-1">Esc</Kbd>
          <span>leave</span>
        </span>
        <div className="ms-auto flex items-center gap-1">
          <Button
            variant="ghost"
            size="sm"
            disabled={currentStep === 0}
            onClick={() => setCurrentStep((current) => Math.max(0, current - 1))}
            data-testid="onboarding-back"
          >
            Back
          </Button>
          <Button
            size="sm"
            onClick={() => (last ? setIsOpen(false) : setCurrentStep((current) => current + 1))}
            data-testid="onboarding-next"
          >
            {last ? "Done" : "Next"}
          </Button>
        </div>
      </div>
    </div>
  );
}

/**
 * The Grove step behind reactour's index. The bridge and the card both need
 * the built list; rebuilding it from the same pinned context is cheaper than
 * threading it through reactour's `meta` string.
 */
function useTourStep(index: number): OnboardingStep | undefined {
  const steps = useOnboardingUi((state) => state.steps);
  return steps[index];
}
