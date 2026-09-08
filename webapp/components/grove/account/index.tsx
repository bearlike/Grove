"use client";

import Link from "next/link";
import {
  BookOpenIcon,
  CompassIcon,
  LogOutIcon,
  MoonIcon,
  SettingsIcon,
  SunIcon,
} from "lucide-react";

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
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { useOnboardingUi } from "@/components/grove/onboarding/onboarding-store";
import { ACCOUNT_ITEMS } from "@/components/grove/shell/nav";
import { useWhoami } from "@/lib/grove/hooks";

/**
 * Where the app points OUTWARD. Both are public by construction — the private
 * forge this repo is developed on must never reach a shipped artifact.
 */
const EXTERNAL_LINKS = [
  {
    href: "https://kanth.tech/Grove",
    label: "Documentation",
    icon: BookOpenIcon,
  },
  {
    href: "https://github.com/bearlike/Grove",
    label: "GitHub repository",
    icon: GitHubIcon,
  },
] as const;

function initials(value: string): string {
  return value.slice(0, 2).toUpperCase();
}

/**
 * The rail footer's identity, destinations and controls.
 *
 * It shares `useWhoami` with the service strip. An unavailable daemon leaves a
 * real menu trigger behind: signed-in status is still true and theme/sign-out
 * actions must remain reachable even while identity details are unknown.
 */
export function AccountMenu({
  collapsed = false,
}: {
  collapsed?: boolean;
}): React.ReactNode {
  const { resolvedTheme, setTheme } = useTheme();
  const whoami = useWhoami();
  const openTour = useOnboardingUi((state) => state.setOpen);

  if (whoami.isPending) {
    return <Skeleton className={cn("h-8", collapsed ? "w-8" : "w-full")} />;
  }

  const identity = whoami.data ?? null;
  const user = identity?.user ?? "Signed in";
  const dark = resolvedTheme === "dark";
  const label = identity ? `${identity.user} on ${identity.host}` : user;

  return (
    <TooltipProvider delayDuration={0}>
      <DropdownMenu>
        <Tooltip>
          <TooltipTrigger asChild>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="sm"
                aria-label={label}
                data-testid="account-menu"
                className={cn(
                  "min-h-[28px] min-w-[28px] justify-start overflow-hidden font-normal transition-all duration-200 [@media(pointer:coarse)]:min-h-[44px] [@media(pointer:coarse)]:min-w-[44px]",
                  collapsed ? "w-8 gap-0 px-0" : "w-full gap-2 px-1.5",
                )}
              >
                <Avatar size="sm">
                  <AvatarFallback>{initials(user)}</AvatarFallback>
                </Avatar>
                {collapsed ? null : (
                  <>
                    <span className="min-w-0 truncate text-sm">
                      {identity ? (
                        <UserAtHost user={identity.user} host={identity.host} />
                      ) : (
                        user
                      )}
                    </span>
                    <SettingsIcon
                      aria-hidden
                      className="ml-auto size-3 shrink-0 text-content-tertiary"
                    />
                  </>
                )}
              </Button>
            </DropdownMenuTrigger>
          </TooltipTrigger>
          <TooltipContent side={collapsed ? "right" : "top"}>
            {label}
          </TooltipContent>
        </Tooltip>

        <DropdownMenuContent side="top" align="start" className="w-60">
          <DropdownMenuLabel className="flex min-w-0 flex-col gap-0.5">
            <span className="min-w-0 truncate">
              {identity ? (
                <UserAtHost user={identity.user} host={identity.host} />
              ) : (
                user
              )}
            </span>
            {identity ? (
              <span
                className="min-w-0 truncate text-xs font-normal text-content-tertiary"
                title={`${identity.platform} · Python ${identity.python_version}`}
              >
                {identity.platform}
              </span>
            ) : (
              <span className="text-xs font-normal text-content-tertiary">
                Daemon unreachable
              </span>
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
          <DropdownMenuItem onSelect={() => openTour(true)} data-testid="account-take-tour">
            <CompassIcon />
            Take the tour
          </DropdownMenuItem>

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

          <DropdownMenuItem
            variant="destructive"
            onSelect={() => void signOut()}
          >
            <LogOutIcon />
            Sign out
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </TooltipProvider>
  );
}

/** `user@host`, with each part ranked as identity, location, and punctuation. */
function UserAtHost({
  user,
  host,
}: {
  user: string;
  host: string;
}): React.ReactNode {
  return (
    <span className="min-w-0 truncate" data-testid="user-at-host">
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
