"use client";

import { CircleArrowUpIcon, TagIcon, TimerIcon } from "lucide-react";

import { durationSince, useNow } from "@/components/grove/relative-time";
import { Glyph, Section } from "@/components/grove/shell/footer/primitives";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { SystemFacts } from "@/lib/grove/adapters/footer";

const RELEASES_URL = "https://github.com/bearlike/Grove/releases";

/**
 * UPTIME then VERSION, each closed by its own rule, both pinned right.
 *
 * Version sits at the very end because it is the band's least volatile fact
 * and the one a reader looks up rather than watches — the same reason the
 * project sits at the other end, which is what makes the two ends read as a
 * pair rather than as a row that happened to run out.
 */
export function SystemSection({ system }: { system: SystemFacts | null }): React.ReactNode {
  const now = useNow();
  if (system === null) return null;
  const uptime = system.startedAt
    ? now === null
      ? new Date(system.startedAt).toLocaleString()
      : `up ${durationSince(system.startedAt, now)}`
    : null;

  return (
    <>
      {uptime ? (
        <Section id="footer-uptime" className="shrink-0 border-l">
          <Tooltip>
            <TooltipTrigger asChild>
              <time
                dateTime={system.startedAt ?? undefined}
                title={uptime}
                className="inline-flex min-w-0 items-center gap-1 truncate px-1"
                tabIndex={0}
              >
                <Glyph Icon={TimerIcon} />
                {uptime}
              </time>
            </TooltipTrigger>
            <TooltipContent>{uptime}</TooltipContent>
          </Tooltip>
        </Section>
      ) : null}
      <Section id="footer-system" accent className="shrink-0">
        {system.updateAvailable && system.latestVersion ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <a
                href={RELEASES_URL}
                target="_blank"
                rel="noreferrer"
                className="inline-flex min-w-0 items-center gap-1 truncate px-1 text-success"
              >
                <Glyph Icon={CircleArrowUpIcon} />
                <span className="min-w-0 truncate tabular-nums">v{system.version} → {system.latestVersion}</span>
              </a>
            </TooltipTrigger>
            <TooltipContent>v{system.version} → {system.latestVersion}</TooltipContent>
          </Tooltip>
        ) : (
          <span className="inline-flex min-w-0 items-center gap-1 px-1">
            <Glyph Icon={TagIcon} />
            <span className="min-w-0 truncate tabular-nums">v{system.version}</span>
          </span>
        )}
      </Section>
    </>
  );
}
