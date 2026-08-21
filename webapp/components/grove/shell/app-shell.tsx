"use client";

import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { cn } from "@/lib/utils";
import { RAIL_WIDTH } from "./rail-width";
import { AppSidebar } from "@/components/grove/shell/app-sidebar";
import {
  useSidebarShortcuts,
  useSidebarUi,
} from "@/components/grove/shell/sidebar-state";
import { FleetOverlays, useFleetStream } from "@/components/grove/fleet";

/**
 * The ONE app shell, with assistant-ui's base-demo rail anatomy:
 *
 *     div.relative.flex.h-dvh                     transparent — the page bg
 *     ├── aside.bg-surface-sunken   the rail — w-12 collapsed, w-98, no border
 *     ├── Sheet          the same rail on a phone, full viewport width
 *     └── div.bg-surface-sunken.p-2.md:pl-0       the gutter
 *         └── div.shell-panel                     the content panel — `base`
 *             └── the page, which supplies its own ShellHeader
 *
 * FOUR LAYERS ON FOUR RUNGS, bottom to top: the rail and gutter are `sunken`,
 * this panel is `base`, a card on a page is `raised`, a dialog is `overlay`.
 * The hierarchy comes from depth, which is why the rail has no `border-r`: a
 * rule between two halves of the SAME layer says they are peers, and the rail
 * and the gutter ARE one layer.
 *
 * They used to share `bg-muted/30` — an alpha tint over whatever sat behind it,
 * which is only ever a fraction of a step from its own backdrop. That is how
 * the panel and the gutter ended up 0.0018 apart in luminance the moment the
 * page background moved under them. A named rung is an absolute position, so
 * the step belongs to the ladder rather than to what happens to be underneath.
 *
 * The padding is `p-2 md:pl-0` — three sides at desktop width, because the
 * panel butts against the rail rather than floating clear of it (measured on
 * the demo: the panel's left edge is exactly the rail's right edge). Below
 * `md` the rail is a Sheet and absent from the row, so all four sides apply.
 *
 * The page's header and actions therefore land INSIDE the panel with no change
 * to `ShellHeader` or to any page: a page is still a plain `min-h-0 flex-1`
 * child, it is just one level deeper. That containment is the point — the
 * panel clips to its own radius, so a page's scrollbar is inset in it instead
 * of running down the window edge.
 *
 * Deliberately NOT shadcn's `Sidebar`/`SidebarProvider`. It draws the RAIL as
 * the floating, rounded, separately-elevated object, and the elevation here
 * runs the other way: the rail belongs to the layer underneath, and the page is
 * what sits on top of it. The width transition, the collapse shortcut and the
 * mobile sheet are the three things it gave us for free, and each is a handful
 * of lines here.
 *
 * There is no viewport arithmetic. `webapp/` subtracts a hard-coded header
 * height in two places that must be kept in sync by hand; here the shell is one
 * fixed-height flex row and every page is a `min-h-0 flex-1` child of it.
 *
 * The shell also owns the fleet's single event-stream subscription and its two
 * overlays, because the rail is rendered TWICE — desktop aside and mobile sheet
 * — and each of those must exist exactly once.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const collapsed = useSidebarUi((state) => state.collapsed);
  const mobileOpen = useSidebarUi((state) => state.mobileOpen);
  const setMobileOpen = useSidebarUi((state) => state.setMobileOpen);
  const stream = useFleetStream();
  useSidebarShortcuts();

  // NOTE: there is deliberately no create-dialog watcher here any more. The
  // drawer used to dismiss on the create store opening, because the rail's
  // create action opened a dialog OVER the still-open sheet — measured at
  // 420px as two stacked modals. That action is now a link to `/`, so the
  // link delegation below covers it, and every remaining opener of the dialog
  // lives outside the sheet where it cannot be reached while the sheet is up.
  return (
    <div className="relative flex h-dvh w-full overflow-hidden">
      <aside
        data-testid="app-sidebar"
        data-collapsed={collapsed}
        className={cn(
          "bg-surface-sunken hidden h-full shrink-0 flex-col overflow-hidden transition-[width] duration-200 md:flex",
          // The expanded width lives in `rail-width.ts` — everything inside a
          // rail is `w-full`, and there is now a SECOND rail (the public share
          // view), so the measure is a shared export rather than a literal
          // either one could drift from. The mobile Sheet is unaffected by it.
          //
          // `w-12` collapsed is deliberately untouched by all of this. It is
          // not a width, it is an arithmetic fit: 8px + a 32px icon button +
          // 8px. Every gutter below therefore stays `px-2` while collapsed,
          // and only the expanded state takes the wider one.
          collapsed ? "w-12" : RAIL_WIDTH,
        )}
      >
        <AppSidebar collapsed={collapsed} stream={stream} />
      </aside>

      {/* Full viewport width, not the desktop rail's w-98: collapsed is a
          content-focused affordance for a pointer that can hover the header
          toggle back open, which a phone does not have. Expanded here IS the
          whole surface, so the sheet takes it all — `sm:max-w-none` overrides
          the vendored `SheetContent`'s own `sm:max-w-sm` cap, which would
          otherwise clip the sheet to 384px on a phone wider than the `sm`
          breakpoint (640px) while still narrower than `md` (768px), where the
          hamburger trigger that opens it is the only way in. */}
      <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
        <SheetContent
          side="left"
          className="flex w-full flex-col p-0 sm:max-w-none"
        >
          <SheetTitle className="sr-only">Grove workspaces</SheetTitle>
          {/* Navigating out of the sheet has served its purpose, so the sheet
              closes — otherwise a phone lands on the new page with the drawer
              still covering it. Scoped to links: searching and filtering happen
              INSIDE the drawer and must not dismiss it. Every exit from this
              drawer is now a route, the create action included, so this one
              predicate is the whole rule. */}
          <div
            className="flex min-h-0 flex-1 flex-col"
            onClick={(event) => {
              if (
                event.target instanceof Element &&
                event.target.closest("a[href]")
              ) {
                setMobileOpen(false);
              }
            }}
          >
            <AppSidebar collapsed={false} stream={stream} />
          </div>
        </SheetContent>
      </Sheet>

      {/* The rail and this gutter are ONE plane and take the same rung — that
          is why neither carries a border. `bg-surface-sunken` rather than the
          old `bg-muted/30`: an alpha tint over the page could only ever be a
          fraction of a step away from it, which is how the panel and the gutter
          ended up 0.0018 apart in luminance. A named rung is an absolute
          position, so the step is the ladder's and not an accident of what
          happened to be behind it. */}
      <div className="bg-surface-sunken flex h-full min-w-0 flex-1 flex-col overflow-hidden p-2 md:pl-0">
        <div className="shell-panel flex min-h-0 flex-1 flex-col">
          {children}
        </div>
      </div>

      <FleetOverlays snapshot={stream.snapshot} />
    </div>
  );
}
