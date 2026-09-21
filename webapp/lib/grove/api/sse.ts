import type { DashboardEvent } from "./types";
import type { SubagentFleetData } from "./fleet";

export type GroveStreamEvent = DashboardEvent | { kind: "fleet_snapshot"; fleet: SubagentFleetData };

export type EventStream = {
  close(): void;
};

export type EventStreamHandlers<T = DashboardEvent> = {
  onEvent(event: T): void;
  onOpen?(): void;
  onError?(): void;
};

/** Subscribe to a cookie-authenticated Grove SSE endpoint. */
export function subscribeToEventStream<T = DashboardEvent>(url: string, handlers: EventStreamHandlers<T>): EventStream | null {
  if (typeof EventSource === "undefined") return null;

  const source = new EventSource(url);
  const receive = (event: MessageEvent<string>): void => {
    try {
      handlers.onEvent(JSON.parse(event.data) as T);
    } catch {
      // A malformed upstream frame must not terminate a healthy stream.
    }
  };

  for (const name of [
    "fleet_snapshot",
    "snapshot",
    "session_activity",
    "workspace_changed",
    "catalog_changed",
    "workspace_source_changed",
    "heartbeat",
    "pane_snapshot",
  ]) {
    source.addEventListener(name, receive as EventListener);
  }
  source.onopen = (): void => handlers.onOpen?.();
  source.onerror = (): void => handlers.onError?.();
  return source;
}
