"use client";

import { RefreshCwIcon, ShieldCheckIcon } from "lucide-react";

import { LangfuseMark } from "@/components/grove/icons/langfuse-mark";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useWhoami } from "@/lib/grove/hooks";
import { cn } from "@/lib/utils";
import type { UsageCoverageView } from "@/lib/grove/api";
import { AbbreviatedNumber } from "./abbreviated-number";
import { SectionSkeleton, UsageSection } from "./section";
import { timestamp } from "./format";
import { sourceHealthTone } from "./tokens";

/**
 * What every number above is BASED ON, and the control that changes it.
 *
 * A page rendered from a half-indexed store is pixel-identical to one rendered
 * from a complete store, and the conclusion a reader draws from it is wrong for
 * a reason nothing else on screen can reveal. So each source is named with its
 * health and its one-line detail — "1 source degraded" tells you nothing you
 * can act on, ".claude: transcript cwd was not measured" tells you which
 * profile to go look at.
 *
 * THE SAME ARGUMENT, AT ITS LIMIT: an UNINDEXED store and a store holding
 * genuinely no usage render identically too — every figure on the page reads
 * empty either way — and only one of them has a remedy. That is not a
 * hypothetical. The usage store is a derived cache with no migrations, so
 * bumping its schema version DISCARDS it, nothing reindexes on its own, and the
 * page then reads empty indefinitely while looking exactly like a quiet month.
 * It happened on 2026-08-11 and cost a real diagnosis. `coverageState` is the
 * one place that distinction is made.
 */

/** Whether the numbers on this page rest on anything, and if not, why not. */
type CoverageState = "unindexed" | "indexed";

/**
 * `last_refresh_at` is the discriminator, NOT the source count.
 *
 * A completed index that found nothing still stamps a refresh time, so the
 * stamp answers "has an index ever been built" while the source list only
 * answers "did it find anything". Reading emptiness off the sources alone
 * conflates the two and tells a user with genuinely no usage to press a button
 * that will change nothing.
 */
function coverageState(coverage: UsageCoverageView): CoverageState {
  return coverage.last_refresh_at ? "indexed" : "unindexed";
}
export function UsageCoverage({
  coverage,
  failed,
  onRetry,
  retrying,
  onRefresh,
  refreshing,
  note,
  className,
}: {
  coverage: UsageCoverageView | undefined;
  failed: boolean;
  onRetry?: () => void;
  retrying?: boolean;
  onRefresh: () => void;
  refreshing: boolean;
  note: string | null;
  className?: string;
}): React.ReactNode {
  const whoami = useWhoami();
  const langfuseHost = whoami.data?.langfuse_host;
  return (
    <UsageSection
      icon={<ShieldCheckIcon />}
      title="Coverage"
      description={coverage ? describeCoverage(coverage) : undefined}
      failed={failed}
      onRetry={onRetry}
      retrying={retrying}
      failure="Coverage could not be read, so nothing on this page can be dated."
      loading={!coverage}
      skeleton={<SectionSkeleton rows={1} />}
      action={
        <div className="flex items-center gap-2">
          {/* Only when Langfuse is genuinely configured — a live daemon whose
              telemetry.enabled=false or whose credentials are partial has no
              host to send anyone to, and a dead link is worse than no
              button. `_resolve_langfuse_host` (daemon) already applies that
              gate, so the browser's only job is to check for `null`. */}
          {langfuseHost ? <OpenInLangfuse host={langfuseHost} /> : null}
          <Button variant="outline" size="sm" onClick={onRefresh} disabled={refreshing}>
            <RefreshCwIcon aria-hidden className={cn("size-4", refreshing && "animate-spin")} />
            {refreshing ? "Refreshing…" : "Refresh"}
          </Button>
        </div>
      }
      className={className}
      data-testid="usage-coverage"
    >
      {/* Stated BEFORE the source badges, because when it applies there are no
          badges to read and the row above is the only thing on the card. */}
      {coverage && coverageState(coverage) === "unindexed" ? (
        <p className="text-xs text-content-secondary" data-testid="coverage-unindexed">
          Nothing is indexed yet, so every figure on this page reads empty.
          Refresh builds the index from your transcripts — on a long history
          that can take several minutes.
        </p>
      ) : null}

      {coverage && coverageState(coverage) === "indexed" && coverage.sources.length === 0 ? (
        <p className="text-xs text-content-secondary" data-testid="coverage-empty">
          The index is built and found no usage to report.
        </p>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        {coverage?.sources.map((source) => (
          <Badge
            key={source.source_id}
            variant={sourceHealthTone(source.health)}
            data-health={source.health}
          >
            {source.label}
            <span className="text-content-tertiary">
              <AbbreviatedNumber value={source.session_count} /> sessions
            </span>
          </Badge>
        ))}
      </div>

      {coverage?.sources
        .filter((source) => source.health !== "ok")
        .map((source) => (
          <p key={source.source_id} className="text-xs text-content-secondary">
            {source.label} is {source.health}
            {source.detail ? `: ${source.detail}` : ""}. Totals below understate
            it.
          </p>
        ))}

      {note ? <p className="text-xs text-content-secondary">{note}</p> : null}
    </UsageSection>
  );
}

/**
 * The header line, which must not date a page that rests on nothing.
 *
 * The unindexed case used to render "Indexed —  · events from — to —": three
 * em-dashes in a sentence whose grammar claims an index exists. A reader parses
 * that as "indexed, and quiet", which is the exact wrong conclusion and the one
 * the body text below then has to argue against.
 */
function describeCoverage(coverage: UsageCoverageView): string {
  if (coverageState(coverage) === "unindexed") return "Not indexed yet";
  return `Indexed ${timestamp(coverage.last_refresh_at)} · events from ${timestamp(coverage.earliest_event_at)} to ${timestamp(coverage.latest_event_at)}`;
}

/**
 * Opens Langfuse's own PROJECT LANDING PAGE — the bare configured host, never
 * a deep link to a specific trace or session.
 *
 * Langfuse's UI namespaces every route under `/project/{projectId}/...`, and
 * `projectId` is not something Grove's config carries (`telemetry.*_env`
 * names three env vars: host, public key, secret key — never a project id)
 * NOR something derivable from them without a live, authenticated call to
 * Langfuse's own API (`GET /api/public/projects`, resolved by the Langfuse
 * SDK itself on first use and then cached). Grove makes no such call today,
 * so the honest destination is the host's own landing page: a user who is
 * already logged in lands on their project picker in one click, and a dead
 * link into a project Grove cannot name is avoided entirely.
 */
function OpenInLangfuse({ host }: { host: string }): React.ReactNode {
  const label = "Open in Langfuse";
  return (
    <Button variant="outline" size="sm" asChild>
      <a href={host} target="_blank" rel="noreferrer" title={label} aria-label={label}>
        <LangfuseMark className="size-4" />
        Langfuse
      </a>
    </Button>
  );
}
