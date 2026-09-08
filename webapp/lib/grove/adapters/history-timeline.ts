import type {
  NameChangeView,
  ProgressEntryView,
  RecordedTicketView,
  WorkspaceHistoryView,
} from "../api";

export type HistoryKind = "progress" | "name" | "ticket";
export type HistoryFilter = "all" | HistoryKind;

type EventSource =
  | { readonly kind: "progress"; readonly entry: ProgressEntryView }
  | { readonly kind: "name"; readonly entry: NameChangeView }
  | { readonly kind: "ticket"; readonly entry: RecordedTicketView };

export type HistoryEvent = EventSource & {
  readonly id: string;
  readonly recordedAt: string;
};

/** Merge observations without inventing causal order between equal timestamps. */
export function historyTimeline(history: WorkspaceHistoryView): HistoryEvent[] {
  const sources: EventSource[] = [
    ...(history.progress ?? []).map((entry) => ({
      kind: "progress" as const,
      entry,
    })),
    ...(history.names ?? []).map((entry) => ({ kind: "name" as const, entry })),
    ...(history.tickets ?? []).map((entry) => ({
      kind: "ticket" as const,
      entry,
    })),
  ];
  const occurrences = new Map<string, number>();
  return sources
    .map((source) => {
      const recordedAt =
        source.kind === "ticket"
          ? source.entry.first_seen
          : source.entry.recorded_at;
      // Last-seen updates must not remount a ticket. The other sources are immutable snapshots.
      const identity =
        source.kind === "ticket"
          ? source.entry.ticket_key
          : JSON.stringify(source.entry);
      const key = `${source.kind}:${identity}`;
      const occurrence = occurrences.get(key) ?? 0;
      occurrences.set(key, occurrence + 1);
      const timestamp = Date.parse(recordedAt);
      return {
        event: { ...source, recordedAt, id: `${key}:${occurrence}` },
        timestamp: Number.isNaN(timestamp) ? -Infinity : timestamp,
      };
    })
    .sort((a, b) => {
      // Stable sort keeps each source's wire order when the clock cannot distinguish rows.
      if (a.timestamp === b.timestamp) return 0;
      return a.timestamp > b.timestamp ? -1 : 1;
    })
    .map(({ event }) => event);
}

/** Search the complete chronology before the presentation limits mounted rows. */
export function filterHistory(
  events: readonly HistoryEvent[],
  kind: HistoryFilter,
  query: string,
): readonly HistoryEvent[] {
  const needle = query.trim().toLowerCase();
  return events.filter((event) => {
    if (kind !== "all" && event.kind !== kind) return false;
    if (!needle) return true;
    let fields: (string | null | undefined)[];
    switch (event.kind) {
      case "progress":
        fields = [
          event.entry.phase,
          event.entry.note,
          event.entry.ticket_key,
          event.entry.blocked ? "blocked" : "",
          "reported progress",
        ];
        break;
      case "name":
        fields = [event.entry.title, event.entry.description, "name recorded"];
        break;
      case "ticket":
        fields = [
          event.entry.ticket_key,
          event.entry.provider,
          event.entry.ticket_id,
          event.entry.kind,
          event.entry.last_seen,
          "ticket first recorded",
        ];
        break;
    }
    return [...fields, event.recordedAt]
      .join(" ")
      .toLowerCase()
      .includes(needle);
  });
}
