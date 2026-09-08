"use client";

import { ArrowUpCircleIcon, TimerIcon } from "lucide-react";

import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
// lucide dropped its brand marks; the vendored icon tree already carries this one.
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { durationSince, useNow } from "@/components/grove/relative-time";
import { useWhoami } from "@/lib/grove/hooks";
import { cn } from "@/lib/utils";
import type { WhoamiView } from "@/lib/grove/api";

/** The public mirror. Deliberately the only URL in this app — the private forge
 * this repo is developed on must never appear in a shipped artifact. */
const GITHUB_URL = "https://github.com/bearlike/Grove";
const RELEASES_URL = `${GITHUB_URL}/releases`;

/**
 * What is running, since when, and whether it is current.
 *
 * The release-skew check is daemon-side and cached for hours, so the browser
 * never independently polls the public release API. `started_at` is an instant,
 * not a decaying second count: `Uptime` can advance it from its existing clock
 * without introducing a second query cadence.
 */
export function DaemonStatus({
  collapsed,
}: {
  collapsed: boolean;
}): React.ReactNode {
  const whoami = useWhoami();

  if (whoami.isPending) {
    return <Skeleton className={cn("h-7", collapsed ? "w-8" : "w-full")} />;
  }
  if (whoami.isError || !whoami.data) return null;

  const identity = whoami.data;
  return (
    <>
      {identity.update_available && identity.latest_version ? (
        <UpdateHint version={identity.latest_version} collapsed={collapsed} />
      ) : null}
      {collapsed ? null : <DaemonServiceStrip identity={identity} />}
    </>
  );
}

/** The compact daemon facts that describe the footer destinations above them. */
export function DaemonServiceStrip({
  identity,
}: {
  identity: WhoamiView;
}): React.ReactNode {
  const now = useNow();
  const uptime = identity.started_at
    ? now === null
      ? new Date(identity.started_at).toLocaleString()
      : `up for ${durationSince(identity.started_at, now)}`
    : "unknown";

  return (
    <TooltipProvider delayDuration={0}>
      <div
        className="flex w-full min-w-0 items-baseline justify-between gap-2"
        data-testid="daemon-service-strip"
      >
        <Tooltip>
          <TooltipTrigger asChild>
            <span
              className="min-w-0 truncate text-xs text-content-tertiary tabular-nums"
              data-testid="daemon-version"
              tabIndex={0}
            >
              v{identity.version}
            </span>
          </TooltipTrigger>
          <TooltipContent>{updateTitle(identity)}</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger asChild>
            <span
              className="max-w-28 shrink-0 truncate text-xs text-content-tertiary"
              data-testid="uptime"
            >
              <TimerIcon
                aria-hidden
                className="mr-1 inline size-[1em] shrink-0 align-[-0.125em]"
              />
              {uptime}
            </span>
          </TooltipTrigger>
          <TooltipContent>{uptime}</TooltipContent>
        </Tooltip>
      </div>
    </TooltipProvider>
  );
}

/**
 * What the version's tooltip says about currency — THREE distinct claims, and
 * the distinction is the whole point.
 *
 * "up to date" is a positive assertion that requires a successful check. When
 * `latest_version` is null the check has not succeeded — offline, first call,
 * or an error — and the only honest thing to say is that we do not know.
 * Collapsing the unknown case into the good case is how a user misses a
 * security release and believes they were told otherwise.
 */
export function updateTitle(identity: WhoamiView): string {
  if (identity.update_available && identity.latest_version) {
    return `Grove ${identity.version} — ${identity.latest_version} is available`;
  }
  if (identity.latest_version) return `Grove ${identity.version} — up to date`;
  return `Grove ${identity.version} — no update information`;
}

/** The one row that appears only when there is something to do about it. */
export function UpdateHint({
  version,
  collapsed,
}: {
  version: string;
  collapsed: boolean;
}): React.ReactNode {
  const label = `Update to ${version}`;
  const control = (
    <Button
      asChild
      variant="ghost"
      size="sm"
      className={cn(
        "min-h-[28px] min-w-[28px] justify-start font-normal [@media(pointer:coarse)]:min-h-[44px] [@media(pointer:coarse)]:min-w-[44px]",
        collapsed
          ? "w-8 gap-0 px-2 has-[>svg]:px-2"
          : "w-full gap-2 px-2.5 has-[>svg]:px-2.5",
      )}
      data-testid="update-hint"
    >
      <a
        href={RELEASES_URL}
        target="_blank"
        rel="noreferrer"
        aria-label={label}
      >
        <ArrowUpCircleIcon className="size-4 shrink-0" />
        {collapsed ? null : <span className="truncate text-xs">{label}</span>}
      </a>
    </Button>
  );

  if (!collapsed) return control;

  return (
    <TooltipProvider delayDuration={0}>
      <Tooltip>
        <TooltipTrigger asChild>{control}</TooltipTrigger>
        <TooltipContent side="right">{label}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
