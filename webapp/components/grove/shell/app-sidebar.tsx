"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { BrandMark } from "@/components/grove/brand-mark";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { RAIL_ITEMS, type NavItem } from "@/components/grove/shell/nav";
import { DaemonStatus } from "@/components/grove/shell/daemon-status";
import { FleetTree, type FleetStream } from "@/components/grove/fleet";
import { AccountMenu } from "@/components/grove/account";

/**
 * What sits inside the rail: brand, the flat workspace list, then the
 * destinations and the account.
 *
 * The base demo's rail carries a brand block and a list and nothing else,
 * because a chat app has one surface. Grove has three, and a collapsed rail is
 * still visible, so the destinations live at the FOOT as icon rows — reachable
 * in both states, and out of the way of the list, which is what the rail is
 * actually for.
 *
 * TWO SPACING RULES GOVERN THE WHOLE RAIL, and both are repeated as literals
 * here and in `fleet-tree.tsx` rather than centralized — the same call the
 * `mt-2` note below makes, and `app-shell.test.ts` asserts the agreement, which
 * is the part that actually stops drift.
 *
 *   gutter   `px-2` collapsed, `p-3` expanded. The expanded value is not a new
 *            number: the footer already used `p-3` and it is the one band of
 *            the rail nobody complained was tight, so it became the measure for
 *            all of them. Collapsed stays `px-2` because it is not a gutter at
 *            all but an arithmetic fit — 8 + a 32px icon + 8 = the 48px rail.
 *   rhythm   `gap-1.5` (6px) between every row, everywhere, replacing `gap-0.5`
 *            (2px). One value across the brand-to-footer column is what lets
 *            the eye read the rail as ranked groups instead of one dense stack;
 *            a group BREAK is then simply a bigger step (see the footer).
 */
export function AppSidebar({
  collapsed,
  stream,
}: {
  collapsed: boolean;
  stream: FleetStream;
}): React.ReactNode {
  const pathname = usePathname();

  return (
    <>
      {/* `mt-2` is the gutter's own `p-2` in `app-shell.tsx`, repeated here on
          purpose. The panel — and `ShellHeader`'s toggle row inside it — sits
          8px down from the viewport top because THAT gutter pads it; this
          row has no such ancestor (the aside carries none), so without the
          offset the brand sat flush in the window's corner while the header
          row it should share a line with started a visible 8px lower. The
          two are on different DOM branches with no parent to align them
          from, so the number is repeated rather than centralized — the one
          thing it has to keep agreeing with is `p-2`, not a derived constant.

          The horizontal gutter is the rail's, so it follows the rail: `px-3`
          expanded, `px-2` collapsed. Collapsed is the constrained one — 8 + the
          mark's own 32px + 8 is exactly the 48px rail, so `px-3` there would
          overflow the mark by 8px and push it off centre. */}
      <div
        className={cn(
          "mt-2 flex h-12 shrink-0 items-center gap-2",
          collapsed ? "px-2" : "px-3",
        )}
      >
        <Link href="/" className="flex min-w-0 items-center gap-2" aria-label="Grove">
          {/* The mark stands FREE — no `Avatar`, reversing the earlier decision
              that let it supply the shape. That was right for a placeholder: a
              generic lucide glyph drawn in `currentColor` has no silhouette of
              its own, so it needed a disc to look deliberate. A real mark has
              one, and the disc it was sitting on is `bg-sidebar-primary` —
              near-black in light mode — which would put brand terracotta on a
              dark circle and mute the one colour in this app that must never
              be muted. Dropping `Avatar` also removes the radius this tree was
              borrowing it for, so the styling gate is satisfied by there being
              no shape decision here at all rather than by delegating one.

              No `label`: the enclosing Link is already `aria-label="Grove"` in
              BOTH rail states, so a labelled mark would be a second name inside
              an element whose name is already fixed. */}
          <BrandMark className="size-8 shrink-0" />
          {collapsed ? null : <span className="truncate text-sm font-medium">Grove</span>}
        </Link>
      </div>

      <FleetTree collapsed={collapsed} stream={stream} />

      {/* The footer reads top-down as: where you can GO, who you ARE, and what
          is RUNNING. The service line sits UNDER the identity deliberately —
          you are signed in as someone, ON a daemon, so version and uptime read
          as properties of the thing you are connected to rather than as a
          fourth destination. It is also the least-consulted row, and the bottom
          is where least-consulted belongs. */}
      <div className={cn("flex shrink-0 flex-col gap-1.5 border-t", collapsed ? "p-2" : "p-3")}>
        {RAIL_ITEMS.map((item) => (
          <NavRow
            key={item.href}
            item={item}
            collapsed={collapsed}
            active={pathname.startsWith(item.href)}
          />
        ))}
        <AccountMenu collapsed={collapsed} />
        {/* THE ONE DELIBERATE BREAK IN THE RHYTHM, and it is what makes the
            rest of it read. Version and uptime are not a fifth row of the
            footer's list — they describe the daemon the four rows above are
            served by — so the step to them is DOUBLE the rhythm (6px gap +
            12px margin = 18px, against 6px between peers). Ranked spacing is
            the whole mechanism here: rows that are siblings sit one step
            apart, and the one that is a different KIND of thing sits two.

            `empty:hidden` because `DaemonStatus` renders NOTHING in two real
            states — collapsed, and daemon unreachable — and a wrapper is a flex
            item whether or not it has content, so without it those states would
            pay the gap and the margin for a row that is not there. The
            component itself returned a bare fragment before, which is why the
            problem did not exist until it acquired a wrapper. The `collapsed`
            gate then covers the one case `empty:hidden` cannot: an update hint
            in the icon rail, which is actionable chrome and not a service
            description, so it stays with its peers. */}
        <div className={cn("flex flex-col gap-1.5 empty:hidden", collapsed ? null : "mt-3")}>
          <DaemonStatus collapsed={collapsed} />
        </div>
      </div>
    </>
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
        "h-8 justify-start overflow-hidden font-normal transition-all duration-200",
        collapsed ? "w-8 gap-0 px-2 has-[>svg]:px-2" : "w-full gap-2 px-2.5 has-[>svg]:px-2.5",
      )}
    >
      <Link href={href} aria-label={label}>
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
    <TooltipProvider delayDuration={0}>
      <Tooltip>
        <TooltipTrigger asChild>{row}</TooltipTrigger>
        {collapsed ? <TooltipContent side="right">{label}</TooltipContent> : null}
      </Tooltip>
    </TooltipProvider>
  );
}
