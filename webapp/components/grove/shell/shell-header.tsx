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
 * It is deliberately NOT a navigation bar — no breadcrumb or separator — and
 * its 32px band ends at one quiet rule. The rule closes the band without turning
 * the header into a second navigation system.
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
    <header
      className="workspace-header flex min-w-0 shrink-0 items-center gap-2 border-b border-border px-4 [@media(pointer:coarse)]:h-14"
      data-testid="shell-header"
    >
      <Button
        variant="outline"
        size="icon"
        className="bg-transparent dark:bg-transparent border-edge-control size-6 min-h-[24px] min-w-[24px] shrink-0 md:hidden [@media(pointer:coarse)]:min-h-11 [@media(pointer:coarse)]:min-w-11"
        onClick={() => setMobileOpen(true)}
        data-testid="shell-sidebar-sheet"
      >
        <MenuIcon className="size-4" />
        <span className="sr-only">Show workspaces</span>
      </Button>

      <TooltipIconButton
        variant="outline"
        size="icon"
        tooltip={collapsed ? "Show sidebar" : "Hide sidebar"}
        side="bottom"
        onClick={toggleCollapsed}
        className="bg-transparent dark:bg-transparent border-edge-control hidden size-6 min-h-[24px] min-w-[24px] shrink-0 md:flex [@media(pointer:coarse)]:min-h-11 [@media(pointer:coarse)]:min-w-11"
        data-testid="shell-sidebar-toggle"
      >
        <PanelLeftIcon className="size-4" />
      </TooltipIconButton>

      {/*
        ONE STEP ABOVE THE NAVIGATION BESIDE IT, and that step is the whole
        hierarchy this band has. It used to be `text-sm` under a `max(12px, …)`
        floor, which rendered it at exactly the same 12px as the pane tabs to
        its right — a title indistinguishable from the controls it titles. Off
        the floor, the three surfaces rank: this at `text-base`, nav at
        `text-sm`, the terminal and diagram sub-bars' metadata at `text-xs`.

        NOT the `text-xl` design-system §1's ramp table assigns to "the page
        title in ShellHeader": that row was written for a masthead, and this is
        a 32px chrome strip whose own rule closes it. An 18px title would be the
        largest thing on a workspace page, above the transcript prose it frames.
        The table is reconciled where it is written rather than here.
      */}
      <span className="min-w-0 truncate text-base font-medium">
        {title ?? sectionFor(pathname).label}
      </span>

      {actions ? <div className="workspace-header-actions ml-auto flex min-w-0 items-center gap-2 self-stretch">{actions}</div> : null}
    </header>
  );
}
