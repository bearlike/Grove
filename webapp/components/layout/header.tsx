import Link from "next/link";
import { ArrowLeft, PanelLeft, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ThemeToggle } from "./theme-toggle";

/**
 * The ONE app-wide header (ADE reframe, #138) — rendered once by the shared
 * shell layout and present on every route (the per-route `<Header>` duplication
 * and the detail page's no-sidebar special case are gone). Sticky, full-bleed on
 * the shared chrome tone (`bg-sidebar/85`), a fixed-height row (h-13 = 52px, the
 * clean-familiar chat-app header height). Borderless since the chrome-teardown
 * (#130): the tone alone separates it from the canvas. All interactive elements
 * compose Button so focus rings, hover behavior, and tap targets stay consistent.
 *
 * The header's middle is a GENERIC slot: a `flex-1` div whose DOM node is handed
 * to the layout via `contextSlotRef`. Because the header lives in the layout, a
 * page can't pass a `context` prop up to it — instead the layout publishes this
 * node through `HeaderSlotContext` and the session page portals its identity +
 * view cluster into it (see `header-slot.tsx`). Every route that portals nothing
 * leaves it an empty spacer.
 *
 * Two sidebar hooks, both optional:
 * - `onOpenSidebar` — the mobile drawer trigger: renders a `lg:hidden` hamburger
 *   that opens the WorkspaceSidebar as a Sheet.
 * - `onToggleSidebar` + `sidebarCollapsed` — the desktop collapse toggle (#88):
 *   an `lg`-only ghost button that flips the persistent rail; the glyph reflects
 *   the current state and the `[` shortcut mirrors it.
 *
 * `back` is the non-landing brand variant: the wordmark + subtitle text
 * disappear (logo only) and an explicit arrow back to `/` takes their place.
 * The #130 teardown reasoned the brand click alone was the "home" affordance;
 * direct product feedback said that was too implicit for users to discover, so
 * any route that ISN'T the landing shell passes `back` for an unambiguous way
 * home. There is only ever one destination (the landing page), so this is a
 * boolean, not a configurable href. The landing shell omits it and keeps the
 * full brand.
 */
export function Header({
  onOpenSidebar,
  onToggleSidebar,
  sidebarCollapsed,
  contextSlotRef,
  back,
}: {
  onOpenSidebar?: () => void;
  onToggleSidebar?: () => void;
  sidebarCollapsed?: boolean;
  /** Callback ref to the header's middle slot; the layout captures it and pages portal a cluster in. */
  contextSlotRef?: (el: HTMLDivElement | null) => void;
  /** Non-landing routes (e.g. `/w/[id]`) set this for a logo-only brand + explicit back-to-home button. */
  back?: boolean;
}) {
  return (
    <header className="sticky top-0 z-30 bg-sidebar/85 pt-[env(safe-area-inset-top)] backdrop-blur-md supports-[backdrop-filter]:bg-sidebar/70">
      {/* Full-width chrome row (no max-width) so the brand sits at the true
          viewport edge, aligned above the flush-left rail, and the actions
          group hugs the right edge. Horizontal padding clears a landscape notch
          via safe-area insets (0 on a non-notched viewport, so no visible change). */}
      <div className="flex h-13 w-full items-center justify-between gap-3 pl-[max(0.75rem,env(safe-area-inset-left))] pr-[max(0.75rem,env(safe-area-inset-right))]">
        <div className="flex items-center gap-1.5">
          {onOpenSidebar && (
            <Button
              variant="ghost"
              size="icon-sm"
              className="lg:hidden"
              aria-label="Open workspace sidebar"
              data-testid="sidebar-trigger"
              onClick={onOpenSidebar}
            >
              <PanelLeft />
            </Button>
          )}
          {onToggleSidebar && (
            <Button
              variant="ghost"
              size="icon-sm"
              className="hidden lg:inline-flex"
              aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
              aria-pressed={sidebarCollapsed}
              title="Toggle sidebar ( [ )"
              data-testid="sidebar-collapse-toggle"
              onClick={onToggleSidebar}
            >
              {sidebarCollapsed ? <PanelLeftOpen /> : <PanelLeftClose />}
            </Button>
          )}
          {back && (
            <Button asChild variant="ghost" size="icon-sm" aria-label="Back to all workspaces">
              <Link href="/">
                <ArrowLeft />
              </Link>
            </Button>
          )}
          {back ? (
            // The Grove mark, logo-only: the explicit back button above already
            // owns the "return home" affordance, so the mark is a static brand
            // anchor here, not a second link to the same place.
            // Hidden below `sm` so the identity cluster owns the narrow-phone
            // header row — the explicit back arrow already carries "home" there.
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src="/grove-logo.png"
              alt=""
              aria-hidden
              width={26}
              height={26}
              className="hidden size-[26px] shrink-0 sm:block"
            />
          ) : (
            <Link
              href="/"
              aria-label="Grove home"
              className="group flex items-center gap-2.5 rounded-md px-1 -mx-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
            >
              {/* The Grove mark (docs/logos/grove-logo.png → public/), the brand
                  anchor across header, favicon, and home screen. */}
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src="/grove-logo.png"
                alt=""
                aria-hidden
                width={26}
                height={26}
                className="size-[26px] shrink-0 transition-transform group-hover:rotate-[-8deg]"
              />
              <span className="flex flex-col gap-px leading-none">
                <span className="text-sm font-semibold leading-none tracking-tight">Grove</span>
                <span className="hidden text-[10px] uppercase leading-none tracking-wider text-muted-foreground sm:inline">
                  workspace dashboard
                </span>
              </span>
            </Link>
          )}
        </div>
        <div ref={contextSlotRef} className="flex min-w-0 flex-1 items-center gap-2" />
        <nav className="flex items-center gap-1.5" aria-label="Site actions">
          {/* No create button here — the composer at the top of the main column is
              the single create affordance (#96 deliverable A). The GitHub link
              lives once, in the status bar; the header keeps only the theme
              toggle so the action group stays a single quiet control. */}
          <ThemeToggle />
        </nav>
      </div>
    </header>
  );
}
