"use client";

import { useState } from "react";
import { AlarmClockIcon } from "lucide-react";

import { CardDisclosure, CardScroll, CardShell } from "@/components/grove/card";
import { WatchStateBadge } from "@/components/grove/fleet/badges";
import { WATCH_STATES, watchLabel } from "@/components/grove/fleet/tokens";
import {
  absoluteTime,
  durationUntil,
  RelativeTime,
  useNow,
} from "@/components/grove/relative-time";
import { orderWatches, watchSubject } from "@/lib/grove/adapters";
import type { WatchList, WatchView } from "@/lib/grove/api";

/**
 * The watches this workspace has armed — the callbacks it halted to wait for.
 *
 * Composed exactly like the Queue card beside it (`CardShell` +
 * `CardDisclosure`), and closed by default for the same reason: the summary
 * already announces how many are running, and a list that opened on its own
 * would push the composer down while someone is typing. Renders nothing when
 * the workspace has never registered a watch — an empty card would claim
 * there is something here to look at.
 */
export function WatchesPanel({
  watches,
  defaultOpen = false,
}: {
  watches: WatchList;
  defaultOpen?: boolean;
}): React.ReactNode {
  const [open, setOpen] = useState(defaultOpen);
  // Optional in the generated type (the contract's `default_factory`), though
  // the daemon always sends it.
  const rows = orderWatches(watches.watches ?? []);
  if (rows.length === 0) return null;

  return (
    <CardShell data-testid="watch-card" data-collapsed={!open}>
      <CardDisclosure
        open={open}
        onOpenChange={setOpen}
        header
        summary={
          <>
            <AlarmClockIcon
              aria-hidden
              className="size-4 shrink-0 text-content-tertiary"
            />
            <span className="shrink-0 text-sm font-medium">Watches</span>
            <span
              className="ms-auto shrink-0 text-xs text-content-tertiary tabular-nums"
              data-testid="watch-summary"
            >
              {summaryLabel(rows)}
            </span>
          </>
        }
      >
        <CardScroll className="divide-y divide-border" data-testid="watch-list">
          {rows.map((watch) => (
            <WatchRow key={watch.id} watch={watch} />
          ))}
        </CardScroll>
      </CardDisclosure>
    </CardShell>
  );
}

/** "2 running · 1 fired" — table order, states with no rows omitted. */
function summaryLabel(rows: readonly WatchView[]): string {
  return WATCH_STATES.map(
    (state) =>
      [state, rows.filter((row) => row.state === state).length] as const,
  )
    .filter(([, count]) => count > 0)
    .map(([state, count]) => `${count} ${watchLabel(state).toLowerCase()}`)
    .join(" · ");
}

function WatchRow({ watch }: { watch: WatchView }): React.ReactNode {
  const subject = watchSubject(watch.predicate);
  // A settled watch's outcome says what happened; a running one has none yet,
  // so its note is what tells a reader which of several it is.
  const detail = watch.outcome?.summary ?? (watch.note || null);
  return (
    <div
      className="min-w-0 px-3 py-2"
      data-testid="watch-row"
      data-state={watch.state}
    >
      <div className="flex min-w-0 items-center gap-1.5">
        <span className="min-w-0 truncate text-sm font-medium" title={subject}>
          {subject}
        </span>
        <WatchStateBadge state={watch.state} />
      </div>
      {detail && (
        <p className="truncate text-xs text-content-secondary" title={detail}>
          {detail}
        </p>
      )}
      <p className="text-xs text-content-tertiary">
        {watch.state === "pending" ? (
          // A standing ticket watch has no deadline: it lasts as long as the
          // workspace runs, so it says so instead of counting down.
          watch.expires_at ? (
            <GivesUpIn iso={watch.expires_at} />
          ) : (
            "Watching while the workspace runs"
          )
        ) : (
          <>
            {watchLabel(watch.state)} <RelativeTime iso={watch.settled_at} />
          </>
        )}
      </p>
    </div>
  );
}

/**
 * How long until a running watch gives up. The server renders the absolute
 * instant and the countdown replaces it after mount — the same hydration rule
 * `RelativeTime` follows, pointed at a future instant.
 */
function GivesUpIn({ iso }: { iso: string }): React.ReactNode {
  const now = useNow();
  return (
    <time dateTime={iso} title={absoluteTime(iso)}>
      Gives up{" "}
      {now === null
        ? `at ${absoluteTime(iso)}`
        : `in ${durationUntil(iso, now)}`}
    </time>
  );
}
