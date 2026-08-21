"use client";

import { TriangleAlertIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { titleRuns } from "@/components/grove/workspace/selectors";
import type { UsageFindingsView } from "@/lib/grove/api";
import { AbbreviatedNumber } from "./abbreviated-number";
import { CardScroll } from "@/components/grove/card";
import { UsageSection } from "./section";
import { humanize } from "./format";

/**
 * The audit's own observations — churn, retries, slow tools.
 *
 * TWO limits, and they are not the same limit. The box bounds the PAGE, which
 * is what stopped this section making the route infinitely scrollable; the cap
 * bounds the DOM, because this store returns 2,475 findings and mounting all of
 * them costs a visible pause on every render for rows nobody scrolls to. The
 * count is stated whenever the cap bites, so the list never silently pretends
 * to be the whole set.
 */
const SHOWN = 100;

export function UsageFindings({
  findings,
  failed,
  onRetry,
  retrying,
  className,
}: {
  findings: UsageFindingsView | undefined;
  failed: boolean;
  onRetry?: () => void;
  retrying?: boolean;
  className?: string;
}): React.ReactNode {
  const all = findings?.findings ?? [];
  const shown = all.slice(0, SHOWN);

  return (
    <UsageSection
      icon={<TriangleAlertIcon />}
      title="Audit findings"
      description={
        all.length > shown.length ? (
          <>
            Showing {shown.length} of <AbbreviatedNumber value={all.length} />, strongest first
          </>
        ) : all.length > 0 ? (
          `${all.length} findings`
        ) : undefined
      }
      failed={failed}
      onRetry={onRetry}
      retrying={retrying}
      failure="Findings could not be loaded."
      loading={!findings}
      className={className}
      data-testid="usage-findings"
    >
      {/* `max-h-*` and not `h-*` below: the shared scroll bounds with a
          max-height, so a fixed height would sit under it and clip to 64. */}
      {shown.length > 0 ? (
        <CardScroll className="max-h-72">
          {/* Index, not content: `kind` + `title` collide legitimately — the
              daemon emits one `edit_churn` finding per file and they share a
              title. Nothing here reorders, so the index is stable. */}
          <ul className="flex flex-col divide-y">
            {shown.map((finding, index) => (
              <li
                key={index}
                className="flex min-w-0 items-center gap-2 py-1.5 text-sm"
                title={finding.detail ?? undefined}
              >
                <Badge variant="secondary" className="shrink-0">
                  {humanize(finding.kind)}
                </Badge>
                <span className="min-w-0 flex-1 truncate"><FindingTitle title={finding.title} /></span>
                <span className="shrink-0 text-xs text-content-tertiary">
                  <AbbreviatedNumber value={finding.count} />
                </span>
              </li>
            ))}
          </ul>
        </CardScroll>
      ) : (
        <p className="text-sm text-content-tertiary">
          No findings were measured for this selection.
        </p>
      )}
    </UsageSection>
  );
}

/** Titles originate with the deterministic audit; paired backticks mark literal subjects. */
function FindingTitle({ title }: { title: string }): React.ReactNode {
  return (
    <>
      {titleRuns(title).map((run, index) =>
        run.code ? (
          <code key={index} className="font-mono">
            {run.text}
          </code>
        ) : (
          <span key={index}>{run.text}</span>
        ),
      )}
    </>
  );
}
