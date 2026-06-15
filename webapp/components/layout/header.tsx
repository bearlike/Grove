import Link from "next/link";
import { Github, PanelLeft, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ThemeToggle } from "./theme-toggle";

const REPO_URL = "https://github.com/bearlike/Grove";

/**
 * App-wide header. Sticky, full-bleed on the shared chrome tone
 * (`bg-sidebar/85`), dividing the brand group from the actions group
 * with a fixed-height row (h-12) and a subtle bottom border. All
 * interactive elements compose Button so focus rings, hover behavior,
 * and tap targets stay consistent.
 *
 * Two sidebar hooks, both optional so routes with no sidebar (detail, login)
 * render the header verbatim:
 * - `onOpenSidebar` — the mobile drawer trigger: renders a `lg:hidden` hamburger
 *   that opens the WorkspaceSidebar as a Sheet.
 * - `onToggleSidebar` + `sidebarCollapsed` — the desktop collapse toggle (#88):
 *   an `lg`-only ghost button that flips the persistent rail; the glyph reflects
 *   the current state and the `[` shortcut (owned by `useSidebarState`) mirrors it.
 */
export function Header({
  onOpenSidebar,
  onToggleSidebar,
  sidebarCollapsed,
}: {
  onOpenSidebar?: () => void;
  onToggleSidebar?: () => void;
  sidebarCollapsed?: boolean;
}) {
  return (
    <header className="sticky top-0 z-30 border-b border-sidebar-border bg-sidebar/85 backdrop-blur-md supports-[backdrop-filter]:bg-sidebar/70">
      {/* Full-width chrome row (no max-width) so the brand sits at the true
          viewport edge, aligned above the flush-left rail, and the actions
          group hugs the right edge. */}
      <div className="flex h-12 w-full items-center justify-between gap-3 px-3">
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
              <span className="hidden text-[10px] uppercase leading-none tracking-[0.08em] text-muted-foreground sm:inline">
                workspace dashboard
              </span>
            </span>
          </Link>
        </div>
        <nav className="flex items-center gap-1.5" aria-label="Site actions">
          {/* No create button here — the composer at the top of the main column is
              the single create affordance (#96 deliverable A). */}
          <Button asChild variant="ghost" size="icon-sm" aria-label="Open Grove on GitHub">
            <a href={REPO_URL} target="_blank" rel="noopener noreferrer">
              <Github />
            </a>
          </Button>
          <span aria-hidden className="mx-0.5 hidden h-4 w-px bg-sidebar-border sm:block" />
          <ThemeToggle />
        </nav>
      </div>
    </header>
  );
}
