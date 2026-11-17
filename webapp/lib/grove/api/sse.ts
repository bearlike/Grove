import type { DashboardEvent, LivePulseView } from "./types";
import type { SubagentFleetData } from "./fleet";

export type GroveStreamEvent = DashboardEvent | { kind: "fleet_snapshot"; fleet: SubagentFleetData };

/** One `live_pulse` frame from `/workspaces/{id}/live`: the bare pulse, no envelope. */
export type LivePulseEvent = LivePulseView;

export type EventStream = {
  close(): void;
};

export type EventStreamHandlers<T = DashboardEvent> = {
  onEvent(event: T, lastEventId: string): void;
  onOpen?(): void;
  onError?(): void;
};

/** Subscribe to a cookie-authenticated Grove SSE endpoint. */
export function subscribeToEventStream<T = DashboardEvent>(url: string, handlers: EventStreamHandlers<T>): EventStream | null {
  if (typeof EventSource === "undefined") return null;

  const source = new EventSource(url);
  const receive = (event: MessageEvent<string>): void => {
    try {
      handlers.onEvent(JSON.parse(event.data) as T, event.lastEventId);
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
    "live_pulse",
  ]) {
    source.addEventListener(name, receive as EventListener);
  }
  source.onopen = (): void => handlers.onOpen?.();
  source.onerror = (): void => handlers.onError?.();
  return source;
}
