"use client";

import type { ReactNode } from "react";
import { CircleAlertIcon } from "lucide-react";

import { SectionCard } from "@/components/grove/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

/**
 * One panel of the audit: the app's card, plus the three states this page needs.
 *
 * The card itself is `SectionCard` — this adds nothing to how it looks and owns
 * only the switch. Every section on this page loads from its OWN query, so each
 * has to be able to be empty, broken or pending while its neighbours are fine;
 * blanking the page on the slowest query is what made the old one feel broken.
 * Holding that switch in one place is also what keeps a failed section the same
 * size as a loaded one, so nothing jumps as the six queries land.
 *
 * It used to compose the vendored `Card` itself, at its own density and type
 * size, which is how the audit ended up looking like a different application
 * from the workspace page.
 */
export function UsageSection({
  failed,
  failure,
  loading,
  skeleton,
  onRetry,
  retrying,
  children,
  ...card
}: React.ComponentProps<typeof SectionCard> & {
  failed?: boolean;
  failure?: string;
  loading?: boolean;
  skeleton?: ReactNode;
  /** The one action an error state owes the reader. Omit it where nothing here
   * can retry — a button that does nothing is worse than no button. */
  onRetry?: () => void;
  retrying?: boolean;
}): React.ReactNode {
  return (
    <SectionCard {...card}>
      {failed ? (
        <SectionFailure detail={failure} onRetry={onRetry} retrying={retrying} />
      ) : loading ? (
        (skeleton ?? <SectionSkeleton />)
      ) : (
        children
      )}
    </SectionCard>
  );
}

/**
 * A section that BROKE — deliberately not the same treatment as one that has
 * nothing to report.
 *
 * Every absence on this page is quiet and neutral, because an unmeasured figure
 * is not a fault. A failed request looks identical under that treatment, and a
 * reader draws a conclusion about their own spend from what was actually a
 * network error. So this one carries a destructive-toned mark and the one
 * action that might fix it.
 *
 * The colour is the SECOND carrier, never the first: the glyph and the sentence
 * say it on their own, which is what keeps the state legible in greyscale and to
 * a reader who cannot separate red from green.
 *
 * Exported because two sections of this page are grids of tiles rather than one
 * card, so they cannot route their failure through `UsageSection` — and without
 * this they each grew their own wording.
 */
export function SectionFailure({
  detail,
  onRetry,
  retrying = false,
}: {
  detail?: string;
  onRetry?: () => void;
  retrying?: boolean;
}): React.ReactNode {
  return (
    <div role="alert" className="flex flex-col items-start gap-2">
      {/* SECONDARY, not tertiary. The tertiary tier is for things that are
          present but not read, and an error is the one thing on the card that
          has to be. The destructive colour stays on the GLYPH — the signal —
          and the sentence explaining it stays neutral, which is what keeps a
          failed section from reading as a page-wide alarm. */}
      <p className="flex items-start gap-2 text-sm text-content-secondary">
        <CircleAlertIcon aria-hidden className="mt-0.5 size-4 shrink-0 text-destructive" />
        <span>{detail ?? "This section could not be loaded."}</span>
      </p>
      {onRetry ? (
        <Button variant="outline" size="sm" onClick={onRetry} disabled={retrying}>
          {retrying ? "Retrying…" : "Try again"}
        </Button>
      ) : null}
    </div>
  );
}

/** Not a card — the placeholder that stands in for a section's rows. */
export function SectionSkeleton({ rows = 3 }: { rows?: number }): React.ReactNode {
  return (
    <div className="flex flex-col gap-2" aria-hidden>
      {Array.from({ length: rows }, (_, index) => (
        <Skeleton key={index} className="h-5 w-full" />
      ))}
    </div>
  );
}
