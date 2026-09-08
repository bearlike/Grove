"use client";

import { useMemo, useRef, useState } from "react";
import { HistoryIcon, PencilIcon, TicketIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import {
  PHASE_BLOCKED_ICON,
  phaseGlyph,
  phaseLabel,
} from "@/components/grove/fleet/tokens";
import {
  filterHistory,
  type HistoryEvent,
  type HistoryFilter,
} from "@/lib/grove/adapters/history-timeline";
import type { TaskPhase } from "@/components/grove/fleet/types";
import { absoluteTime, preciseAge, useNow } from "@/components/grove/relative-time";
import { cn } from "@/lib/utils";

const BATCH_SIZE = 50;
const EMPTY_TICKET_URLS: ReadonlyMap<string, string> = new Map();
const LOCAL_DATE = new Intl.DateTimeFormat("en-CA", {
  calendar: "gregory",
  day: "2-digit",
  month: "2-digit",
  numberingSystem: "latn",
  year: "numeric",
  timeZone: undefined,
});
const LOCAL_TIME = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });

const FILTERS: readonly { readonly value: HistoryFilter; readonly label: string }[] = [
  { value: "all", label: "All" },
  { value: "progress", label: "Progress" },
  { value: "name", label: "Names" },
  { value: "ticket", label: "Tickets" },
];

/** A client-local rendering of durable observations, without another fetch. */
export function HistoryFeed({
  events,
  ticketUrls = EMPTY_TICKET_URLS,
}: {
  events: readonly HistoryEvent[];
  ticketUrls?: ReadonlyMap<string, string>;
}): React.ReactNode {
  const [filter, setFilter] = useState<HistoryFilter>("all");
  const [query, setQuery] = useState("");
  const [visible, setVisible] = useState(BATCH_SIZE);
  const now = useNow();
  const scrollRef = useRef<HTMLDivElement>(null);
  const filtered = useMemo(() => filterHistory(events, filter, query), [events, filter, query]);
  const shown = filtered.slice(0, visible);
  const groups = groupByLocalDate(shown);

  function resetPresentation(): void {
    setVisible(BATCH_SIZE);
    scrollRef.current?.scrollTo({ top: 0 });
  }

  function selectFilter(next: HistoryFilter): void {
    setFilter(next);
    resetPresentation();
  }

  function search(next: string): void {
    setQuery(next);
    resetPresentation();
  }

  function clearFilters(): void {
    setFilter("all");
    setQuery("");
    resetPresentation();
  }

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-3">
      <div className="flex min-w-0 flex-col gap-2">
        <Input
          aria-label="Search history"
          type="search"
          value={query}
          onChange={(event) => search(event.target.value)}
          placeholder="Search notes, phases, names or ticket IDs"
        />
        <div className="flex flex-wrap gap-2" role="group" aria-label="History type">
          {FILTERS.map(({ value, label }) => (
            <Button
              key={value}
              variant={filter === value ? "secondary" : "outline"}
              size="sm"
              aria-pressed={filter === value}
              onClick={() => selectFilter(value)}
            >
              {label}
            </Button>
          ))}
        </div>
        <p className="flex justify-between gap-2 text-xs text-content-tertiary">
          <span>Local time</span>
          <span>Newest first</span>
        </p>
      </div>
      <Separator />
      <div ref={scrollRef} className="max-h-[60vh] min-h-0 min-w-0 overflow-y-auto pr-1">
        {filtered.length === 0 ? (
          <div className="flex flex-col items-start gap-2 py-3">
            <p className="text-sm text-content-secondary">No matching events</p>
            <Button variant="outline" size="sm" onClick={clearFilters}>
              Clear filters
            </Button>
          </div>
        ) : (
          <div className="flex min-w-0 flex-col gap-4">
            {groups.map((group) => (
              <section key={group.date} className="flex min-w-0 flex-col gap-2">
                <h3 className="text-xs font-medium text-content-secondary">{group.date}</h3>
                <ol className="flex min-w-0 flex-col">
                  {group.events.map((event, index) => (
                    <HistoryEventRow
                      key={event.id}
                      event={event}
                      last={index === group.events.length - 1}
                      ticketUrl={ticketUrl(event, ticketUrls)}
                      now={now}
                    />
                  ))}
                </ol>
              </section>
            ))}
          </div>
        )}
      </div>
      <div className="flex min-w-0 items-center justify-between gap-2 border-t border-border pt-3">
        <p className="text-xs text-content-tertiary">
          Showing {shown.length} of {filtered.length} recorded events
        </p>
        {shown.length < filtered.length && (
          <Button variant="outline" size="sm" onClick={() => setVisible((count) => count + BATCH_SIZE)}>
            Show more
          </Button>
        )}
      </div>
    </div>
  );
}

function HistoryEventRow({
  event,
  last,
  ticketUrl,
  now,
}: {
  event: HistoryEvent;
  last: boolean;
  ticketUrl: string | null;
  now: number | null;
}): React.ReactNode {
  const presentation = eventPresentation(event);
  const Glyph = presentation.Glyph;

  return (
    <li
      className="grid min-w-0 grid-cols-[7rem_1rem_minmax(0,1fr)] gap-x-2 pb-3 last:pb-0 max-sm:grid-cols-[1rem_minmax(0,1fr)]"
      data-testid="history-event"
    >
      <span className="history-event-time col-start-1 row-start-1 flex flex-col items-end text-xs text-content-tertiary tabular-nums max-sm:col-start-2 max-sm:row-start-2 max-sm:mt-1 max-sm:flex-row max-sm:items-center max-sm:gap-1 max-sm:text-left">
        <time
          dateTime={event.recordedAt}
          title={event.recordedAt}
          aria-label={`Recorded ${event.recordedAt}`}
        >
          {localTime(event.recordedAt)}
        </time>
        <time
          dateTime={event.recordedAt}
          title={absoluteTime(event.recordedAt)}
          className="whitespace-nowrap"
        >
          {now === null ? "—" : preciseAge(event.recordedAt, now)}
        </time>
      </span>
      <span className="history-feed-rail col-start-2 row-start-1 row-span-2 flex min-h-full justify-start max-sm:col-start-1" data-last={last || undefined}>
        <span className="history-feed-mark" data-tone={presentation.tone} aria-hidden>
          <Glyph />
        </span>
      </span>
      <div className="col-start-3 row-start-1 flex min-w-0 flex-col gap-1 max-sm:col-start-2">
        <p className="min-w-0 break-words text-sm font-medium text-content-primary">
          {event.kind === "ticket" ? (
            <>
              Ticket first recorded · <TicketReference url={ticketUrl}>{event.entry.ticket_key}</TicketReference>
            </>
          ) : (
            presentation.title
          )}
        </p>
        {presentation.detail && <p className="min-w-0 break-words text-sm text-content-secondary">{presentation.detail}</p>}
        {presentation.meta && (
          <p className="min-w-0 break-words text-xs text-content-tertiary">
            {event.kind === "progress" ? (
              <TicketReference url={ticketUrl}>{presentation.meta}</TicketReference>
            ) : (
              presentation.meta
            )}
          </p>
        )}
      </div>
    </li>
  );
}

function TicketReference({
  children,
  url: href,
}: {
  children: React.ReactNode;
  url: string | null;
}): React.ReactNode {
  if (!href) return children;
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      title="Opens in a new tab"
      aria-description="Opens in a new tab"
      className="font-mono text-xs tabular-nums text-content-primary underline decoration-dotted underline-offset-2 hover:decoration-solid"
    >
      {children}
    </a>
  );
}

function ticketUrl(event: HistoryEvent, ticketUrls: ReadonlyMap<string, string>): string | null {
  const key = event.kind === "progress" ? event.entry.ticket_key : event.kind === "ticket" ? event.entry.ticket_key : null;
  const resolved = key ? ticketUrls.get(key) : undefined;
  return resolved && isHttpUrl(resolved) ? resolved : null;
}

function isHttpUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:";
  } catch {
    return false;
  }
}

function eventPresentation(event: HistoryEvent): {
  title: string;
  detail: string | null;
  meta: string | null;
  Glyph: typeof HistoryIcon;
  tone?: "blocked" | "done";
} {
  switch (event.kind) {
    case "progress": {
      const phase = event.entry.phase;
      const known = isKnownPhase(phase);
      const label = known ? phaseLabel(phase) : (phase ?? "No phase");
      return {
        title: `Reported ${label}${event.entry.blocked ? " · Blocked" : ""}`,
        detail: event.entry.note,
        meta: event.entry.ticket_key,
        Glyph: event.entry.blocked ? PHASE_BLOCKED_ICON : known ? phaseGlyph(phase) : HistoryIcon,
        tone: event.entry.blocked ? "blocked" : phase === "done" ? "done" : undefined,
      };
    }
    case "name":
      return {
        title: `Name recorded · ${event.entry.title}`,
        detail: event.entry.description,
        meta: null,
        Glyph: PencilIcon,
      };
    case "ticket":
      return {
        title: `Ticket first recorded · ${event.entry.ticket_key}`,
        detail: ticketKind(event.entry.kind),
        meta: `First recorded ${localDateTime(event.entry.first_seen)} · Last observed ${localDateTime(event.entry.last_seen)}`,
        Glyph: TicketIcon,
      };
  }
}

function groupByLocalDate(events: readonly HistoryEvent[]): readonly {
  readonly date: string;
  readonly events: readonly HistoryEvent[];
}[] {
  const groups = new Map<string, HistoryEvent[]>();
  for (const event of events) {
    const date = localDate(event.recordedAt);
    const group = groups.get(date);
    if (group) group.push(event);
    else groups.set(date, [event]);
  }
  return [...groups].map(([date, grouped]) => ({ date, events: grouped }));
}

function localDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "Unknown date";
  const parts = LOCAL_DATE.formatToParts(date);
  const part = (kind: Intl.DateTimeFormatPartTypes): string =>
    parts.find(({ type }) => type === kind)?.value ?? "00";
  return `${part("year")}-${part("month")}-${part("day")}`;
}

function localTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "Unknown time";
  return LOCAL_TIME.format(date);
}

function localDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return `${localDate(value)} at ${localTime(value)}`;
}

function ticketKind(kind: string): string {
  return kind ? `${kind.slice(0, 1).toLocaleUpperCase()}${kind.slice(1)}` : "Ticket";
}

function isKnownPhase(phase: string | null): phase is TaskPhase {
  return phase === "scoping" || phase === "planning" || phase === "implementing" || phase === "verifying" || phase === "delivering" || phase === "done";
}
