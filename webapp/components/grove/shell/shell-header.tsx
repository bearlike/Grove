"use client";

import { usePathname } from "next/navigation";
import { MenuIcon, PanelLeftIcon } from "lucide-react";

import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { Button } from "@/components/ui/button";
import { sectionFor } from "@/components/grove/shell/nav";
import { useSidebarUi } from "@/components/grove/shell/sidebar-state";

/**
 * The page header: the rail toggle, a title, and whatever the page pins right.
 *
 * It is deliberately NOT a navigation bar — no border, no breadcrumb, no
 * separator, and `h-12` rather than `h-14`. A bar with a rule under it reads as
 * a second piece of chrome sitting on the page; assistant-ui's shell puts the
 * page's own surface right up against the rail and lets the header float in it,
 * and that is the whole difference in feel.
 *
 * The title falls back to the route's label, so a page that has nothing more
 * specific to say passes nothing. Only ONE toggle exists, and it lives here
 * rather than in the rail: the rail is what gets hidden, so a control inside it
 * would be the one thing you cannot reach when you need it. Below `md` the
 * rail is a sheet, so the toggle becomes a menu button.
 */
export function ShellHeader({
  title,
  actions,
}: {
  title?: string;
  actions?: React.ReactNode;
}) {
  const pathname = usePathname();
  const collapsed = useSidebarUi((state) => state.collapsed);
  const toggleCollapsed = useSidebarUi((state) => state.toggleCollapsed);
  const setMobileOpen = useSidebarUi((state) => state.setMobileOpen);

  return (
    <header className="flex h-12 shrink-0 items-center gap-2 px-4" data-testid="shell-header">
      <Button
        variant="ghost"
        size="icon"
        className="size-8 shrink-0 md:hidden"
        onClick={() => setMobileOpen(true)}
        data-testid="shell-sidebar-sheet"
      >
        <MenuIcon className="size-4" />
        <span className="sr-only">Show workspaces</span>
      </Button>

      <TooltipIconButton
        variant="ghost"
        size="icon"
        tooltip={collapsed ? "Show sidebar" : "Hide sidebar"}
        side="bottom"
        onClick={toggleCollapsed}
        className="hidden size-8 shrink-0 md:flex"
        data-testid="shell-sidebar-toggle"
      >
        <PanelLeftIcon className="size-4" />
      </TooltipIconButton>

      <span className="min-w-0 truncate text-sm font-medium">
        {title ?? sectionFor(pathname).label}
      </span>

      {actions ? <div className="ml-auto flex shrink-0 items-center gap-2">{actions}</div> : null}
    </header>
  );
}
