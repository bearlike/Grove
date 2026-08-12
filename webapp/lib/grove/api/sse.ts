import type { DashboardEvent } from "./types";

export type EventStream = {
  close(): void;
};

export type EventStreamHandlers = {
  onEvent(event: DashboardEvent): void;
  onOpen?(): void;
  onError?(): void;
};

/** Subscribe to a cookie-authenticated Grove SSE endpoint. */
export function subscribeToEventStream(url: string, handlers: EventStreamHandlers): EventStream | null {
  if (typeof EventSource === "undefined") return null;

  const source = new EventSource(url);
  const receive = (event: MessageEvent<string>): void => {
    try {
      handlers.onEvent(JSON.parse(event.data) as DashboardEvent);
    } catch {
      // A malformed upstream frame must not terminate a healthy stream.
    }
  };

  for (const name of ["snapshot", "session_activity", "workspace_changed", "heartbeat", "pane_snapshot"]) {
    source.addEventListener(name, receive as EventListener);
  }
  source.onopen = (): void => handlers.onOpen?.();
  source.onerror = (): void => handlers.onError?.();
  return source;
}
