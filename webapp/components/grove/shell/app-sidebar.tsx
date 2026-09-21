"use client";

import type { MouseEvent as ReactMouseEvent } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { SearchIcon } from "lucide-react";

import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { ScannedTextScope } from "@/components/grove/overflow-text";
import { RAIL_ITEMS, type NavItem } from "@/components/grove/shell/nav";
import { FleetTree, type FleetStream } from "@/components/grove/fleet";
import { activeFilterCount } from "@/components/grove/fleet/filter";
import type { ProjectContextController } from "@/components/grove/fleet/project-context";
import type { FleetSearchController } from "@/components/grove/fleet/fleet-palette";
import { AccountMenu } from "@/components/grove/account";
import { BrandMark } from "@/components/grove/brand-mark";

/** The rail's two renderings share shell-owned search state but keep their own scanned-text scope. */
export function AppSidebar({
  collapsed,
  stream,
  project,
  search,
  onOpenSearch,
  headerAction,
}: {
  collapsed: boolean;
  stream: FleetStream;
  project: ProjectContextController;
  search: FleetSearchController;
  onOpenSearch(event: ReactMouseEvent<HTMLButtonElement>): void;
  /** The mobile sheet's native close control belongs in this band, not a full-height gutter. */
  headerAction?: React.ReactNode;
}): React.ReactNode {
  const pathname = usePathname();
  const activeFilters = activeFilterCount(search.filter);
  const searchTooltip = activeFilters > 0
    ? `Search workspaces (${activeFilters} filters active)`
    : "Search workspaces";

  return (
    <ScannedTextScope>
      <div
        data-testid="sidebar-brand-header"
        className={cn(
          "workspace-header flex shrink-0 items-center gap-2 [@media(pointer:coarse)]:h-14",
          collapsed ? "px-2 [@media(pointer:coarse)]:px-0.5" : "border-b border-border px-3",
        )}
      >
        {collapsed ? null : (
          <Link
            href="/"
            className="flex min-w-0 flex-1 items-center gap-2"
            aria-label="Grove"
          >
            <BrandMark className="size-7 shrink-0" />
            <span className="truncate text-sm font-medium">Grove</span>
          </Link>
        )}
        <TooltipIconButton
          variant="outline"
          tooltip={searchTooltip}
          aria-label="Search workspaces"
          onClick={onOpenSearch}
          className={cn(
            "bg-transparent dark:bg-transparent border-edge-control size-6 min-h-[24px] min-w-[24px] [@media(pointer:coarse)]:min-h-11 [@media(pointer:coarse)]:min-w-11",
            collapsed && "mx-auto",
          )}
          data-active={activeFilters > 0}
          data-testid="sidebar-search-trigger"
        >
          <SearchIcon />
        </TooltipIconButton>
        {headerAction}
      </div>

      <div className={cn("relative isolate flex min-h-0 flex-1 overflow-hidden", !collapsed && "rail-scroll-depth")}>
        <FleetTree collapsed={collapsed} stream={stream} project={project} search={search} />
      </div>

      <div
        className={cn(
          "flex shrink-0 flex-col gap-1.5 border-t",
          collapsed ? "p-2 [@media(pointer:coarse)]:px-0.5" : "p-3",
        )}
      >
        <div
          className={
            collapsed ? "flex flex-col gap-1.5" : "grid grid-cols-2 gap-1.5"
          }
        >
          {RAIL_ITEMS.map((item) => (
            <NavRow
              key={item.href}
              item={item}
              collapsed={collapsed}
              active={pathname.startsWith(item.href)}
            />
          ))}
        </div>
        <AccountMenu collapsed={collapsed} />
      </div>
    </ScannedTextScope>
  );
}

/** One destination, sharing the list rows' geometry so the rail reads as one column. */
function NavRow({
  item,
  collapsed,
  active,
}: {
  item: NavItem;
  collapsed: boolean;
  active: boolean;
}): React.ReactNode {
  const { href, label, icon: Icon } = item;
  const row = (
    <Button
      asChild
      variant={active ? "secondary" : "ghost"}
      size="sm"
      className={cn(
        "min-h-[28px] min-w-[28px] justify-start overflow-hidden font-normal transition-all duration-200 [@media(pointer:coarse)]:min-h-[44px] [@media(pointer:coarse)]:min-w-[44px]",
        collapsed
          ? "h-8 w-8 gap-0 px-2 has-[>svg]:px-2"
          : "h-8 w-full gap-2 px-2.5 has-[>svg]:px-2.5",
      )}
    >
      <Link href={href} aria-label={label} data-testid={`rail-nav-${label.toLowerCase()}`}>
        <Icon className="size-4" />
        <span
          className={cn(
            "overflow-hidden whitespace-nowrap transition-all duration-200",
            collapsed ? "max-w-0 opacity-0" : "max-w-32 opacity-100",
          )}
        >
          {label}
        </span>
      </Link>
    </Button>
  );

  return (
    <Tooltip>
      <TooltipTrigger asChild>{row}</TooltipTrigger>
      {collapsed ? (
        <TooltipContent side="right">{label}</TooltipContent>
      ) : null}
    </Tooltip>
  );
}
