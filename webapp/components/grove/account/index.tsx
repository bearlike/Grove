"use client";

import Link from "next/link";
import { BookOpenIcon, LogOutIcon, MoonIcon, SunIcon } from "lucide-react";

import { GitHubIcon } from "@/components/icons/github";
import { useTheme } from "next-themes";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { ACCOUNT_ITEMS } from "@/components/grove/shell/nav";

/**
 * Where the app points OUTWARD. Both are public by construction — the private
 * forge this repo is developed on must never reach a shipped artifact.
 *
 * Docs before source: far more people want to know how a thing works than want
 * to read it, and the more common destination goes first.
 */
const EXTERNAL_LINKS = [
  { href: "https://kanth.tech/Grove", label: "Documentation", icon: BookOpenIcon },
  { href: "https://github.com/bearlike/Grove", label: "GitHub repository", icon: GitHubIcon },
] as const;
import { useWhoami } from "@/lib/grove/hooks";

function initials(value: string): string {
  return value.slice(0, 2).toUpperCase();
}

/**
 * The rail footer's identity, destinations and controls.
 *
 * It wears the same row geometry as every other rail row — a ghost `Button`
 * that shrinks to `w-8` when the rail collapses — so the footer reads as the
 * bottom of one column rather than a separate widget bolted underneath.
 *
 * IT IS A MENU, NOT A DASHBOARD. Identity, the destinations that do not earn a
 * permanent rail row, the theme, and the way out. Everything the daemon knows
 * about itself that is not glanceable — the platform string — rides the
 * identity label as secondary text rather than becoming its own row, because a
 * menu that grows a fact per release stops being scannable at about item five.
 *
 * IT SHARES ONE FETCH WITH THE FOOTER. This used to hold its own `useEffect`
 * over `getWhoami`, which was a second request for data the shell already had
 * and — worse — swallowed its error into a permanent skeleton, so a daemon
 * restart left the user staring at a loading bar with no way to sign out.
 * `useWhoami` is the same react-query entry the status row reads, so there is
 * one request, one cache, and a real error state.
 */
export function AccountMenu({ collapsed = false }: { collapsed?: boolean }): React.ReactNode {
  const { resolvedTheme, setTheme } = useTheme();
  const whoami = useWhoami();

  if (whoami.isPending) {
    return <Skeleton className={cn("h-8", collapsed ? "w-8" : "w-full")} />;
  }

  // An unreachable daemon must not cost the user the ability to leave. Identity
  // degrades to what is still true — this is a signed-in session, we just
  // cannot name it — and every control below stays mounted.
  const identity = whoami.data ?? null;
  const user = identity?.user ?? "Signed in";
  const dark = resolvedTheme === "dark";

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          aria-label={identity ? `${identity.user} on ${identity.host}` : user}
          data-testid="account-menu"
          className={cn(
            "h-8 justify-start overflow-hidden font-normal transition-all duration-200",
            collapsed ? "w-8 gap-0 px-0" : "w-full gap-2 px-1.5",
          )}
        >
          <Avatar size="sm">
            <AvatarFallback>{initials(user)}</AvatarFallback>
          </Avatar>
          <span
            className={cn(
              "overflow-hidden text-sm whitespace-nowrap transition-all duration-200",
              collapsed ? "max-w-0 opacity-0" : "max-w-36 truncate opacity-100",
            )}
          >
            {identity ? <UserAtHost user={identity.user} host={identity.host} /> : user}
          </span>
        </Button>
      </DropdownMenuTrigger>

      <DropdownMenuContent side="top" align="start" className="w-60">
        <DropdownMenuLabel className="flex min-w-0 flex-col gap-0.5">
          <span className="min-w-0 truncate">
            {identity ? <UserAtHost user={identity.user} host={identity.host} /> : user}
          </span>
          {identity ? (
            <span
              className="min-w-0 truncate text-xs font-normal text-content-tertiary"
              title={`${identity.platform} · Python ${identity.python_version}`}
            >
              {identity.platform}
            </span>
          ) : (
            <span className="text-xs font-normal text-content-tertiary">Daemon unreachable</span>
          )}
        </DropdownMenuLabel>

        <DropdownMenuSeparator />

        {ACCOUNT_ITEMS.map((item) => (
          <DropdownMenuItem key={item.href} asChild>
            <Link href={item.href}>
              <item.icon />
              {item.label}
            </Link>
          </DropdownMenuItem>
        ))}

        <DropdownMenuSeparator />

        <DropdownMenuItem onSelect={() => setTheme(dark ? "light" : "dark")}>
          {dark ? <SunIcon /> : <MoonIcon />}
          {dark ? "Use light theme" : "Use dark theme"}
        </DropdownMenuItem>

        {/* The two places that leave the app, kept together and separated from
            everything that does not. They sit above Sign out and below the
            in-app destinations because that is the order of decreasing
            reversibility: navigate, leave, end the session. `target="_blank"`
            is the affordance — an outbound link that replaced the app would
            lose whatever the user was watching. */}
        <DropdownMenuSeparator />

        {EXTERNAL_LINKS.map((link) => (
          <DropdownMenuItem key={link.href} asChild>
            <a href={link.href} target="_blank" rel="noreferrer">
              <link.icon />
              {link.label}
            </a>
          </DropdownMenuItem>
        ))}

        <DropdownMenuSeparator />

        <DropdownMenuItem variant="destructive" onSelect={() => void signOut()}>
          <LogOutIcon />
          Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/**
 * `user@host`, with the three parts weighted rather than coloured.
 *
 * WHO you are is the thing being named, so it carries full contrast. WHERE runs
 * one step back — on a single-host install it is constant and reading it every
 * time is wasted attention, while on a fleet of daemons it is the whole point.
 * The `@` is punctuation and steps back furthest: it is structure, not
 * information, and at equal weight three tokens read as one undifferentiated
 * string, which is the same defect the entity glyphs fixed on the rail rows.
 *
 * THREE VALUES, THREE TIERS, AND THAT IS WHY THE OPACITY STEP IS GONE. This
 * ranked correctly and expressed it wrongly: `text-foreground`, then
 * `text-muted-foreground/60`, then `text-muted-foreground` — the `@` was faked
 * as an alpha step *below* the floor because the two tokens available could not
 * express three levels. The content ramp can, exactly: primary, secondary,
 * tertiary. An alpha neutral changes meaning against every background it lands
 * on and a token does not, which matters here because this string renders on
 * two rungs — the rail's `base` and the menu's `overlay`.
 */
function UserAtHost({ user, host }: { user: string; host: string }): React.ReactNode {
  return (
    <span className="min-w-0 truncate" data-testid="user-at-host" title={`${user}@${host}`}>
      <span className="font-medium text-content-primary">{user}</span>
      <span className="text-content-tertiary">@</span>
      <span className="text-content-secondary">{host}</span>
    </span>
  );
}

async function signOut(): Promise<void> {
  await fetch("/api/auth/logout", { method: "POST" });
  window.location.assign("/login");
}
