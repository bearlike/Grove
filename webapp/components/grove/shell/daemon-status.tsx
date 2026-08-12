"use client";

import { ArrowUpCircleIcon } from "lucide-react";

import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
// lucide dropped its brand marks; the vendored icon tree already carries this one.
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Uptime } from "@/components/grove/relative-time";
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
 * WHY THE BROWSER NEVER ASKS GITHUB. The release-skew check is daemon-side and
 * cached for hours (`grove.core.release`), so `update_available` arrives with
 * the identity for free. A client-side fetch would put every open tab on
 * GitHub's rate limit to answer a question the daemon has already answered.
 *
 * WHY UPTIME IS `started_at` AND NOT `uptime_seconds`. A count of seconds is
 * correct exactly once — at the instant it was fetched — and then decays
 * silently until something refetches. An instant is true forever, so
 * `RelativeTime` re-renders it from the browser's own clock every minute and
 * the rail stays honest with no polling at all. That is also why this query has
 * no `refetchInterval`: nothing it shows goes stale on its own.
 *
 * THREE STATES, NOT ONE. Loading is a skeleton the size of the real row.
 * Unreachable renders nothing rather than a wrong version — a stale number
 * presented confidently is worse than an absent one, and the account menu below
 * carries the same error where a user can act on it. And a missing release
 * check says "no update information", NEVER "up to date": those are different
 * claims and only one of them is knowable when the check failed.
 */
export function DaemonStatus({ collapsed }: { collapsed: boolean }): React.ReactNode {
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
      {collapsed ? null : (
        <div className="flex w-full min-w-0 items-center gap-1">
          {/* Tertiary, not primary: a version string is metadata about what you
              are connected to, never content. The same judgement the `@` in
              `user@host` gets — structure and metadata step back so the thing
              you actually came to read stays forward.

              NOT MONO. Mono means "a literal you could retype and have it mean
              the same thing" — a path, a ref, an id. `v0.0.7` is a quantity you
              read, and it is the same class of data the usage page sets in sans
              with `tabular-nums`. The digits still need to align, which is what
              `tabular-nums` is for; the typeface was never carrying that. */}
          <span
            className="text-content-tertiary min-w-0 truncate text-xs tabular-nums"
            data-testid="daemon-version"
            title={updateTitle(identity)}
          >
            v{identity.version}
          </span>
          {/* UPTIME, NOT AN AGE. `RelativeTime` would say "2h ago", which
              places the start in the past and reads as an event; this is a
              duration that is still accruing. Same `started_at` source either
              way — an instant stays true while a second-count decays.
              `max-w-28 truncate` because it renders the ABSOLUTE start instant
              until it mounts, and unbounded that string is wider than the rail. */}
          <Uptime
            iso={identity.started_at}
            className="text-muted-foreground ml-auto max-w-28 shrink-0 truncate text-xs"
          />
        </div>
      )}
    </>
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
 *
 * Exported because that rule is a contract worth a test, not an implementation
 * detail of a tooltip.
 */
export function updateTitle(identity: WhoamiView): string {
  if (identity.update_available && identity.latest_version) {
    return `Grove ${identity.version} — ${identity.latest_version} is available`;
  }
  if (identity.latest_version) return `Grove ${identity.version} — up to date`;
  // The check has not succeeded: offline, first call, or an error. Saying "up
  // to date" here would assert something nobody has established.
  return `Grove ${identity.version} — no update information`;
}

/**
 * The one row that appears only when there is something to do about it.
 *
 * It is a link rather than a badge because an update the reader cannot act on
 * is just an interruption; this goes straight to the releases page. Persistent
 * chrome earns its line by being actionable.
 */
function UpdateHint({
  version,
  collapsed,
}: {
  version: string;
  collapsed: boolean;
}): React.ReactNode {
  const label = `Update to ${version}`;
  return (
    <Button
      asChild
      variant="ghost"
      size="sm"
      className={cn(
        "h-8 justify-start font-normal",
        collapsed ? "w-8 gap-0 px-2 has-[>svg]:px-2" : "w-full gap-2 px-2.5 has-[>svg]:px-2.5",
      )}
      data-testid="update-hint"
    >
      <a href={RELEASES_URL} target="_blank" rel="noreferrer" title={label} aria-label={label}>
        <ArrowUpCircleIcon className="size-4 shrink-0" />
        {collapsed ? null : <span className="truncate text-xs">{label}</span>}
      </a>
    </Button>
  );
}

