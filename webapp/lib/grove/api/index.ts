export { GroveClient, GroveProtocolError } from "./client";
export { PublicClient, publicClient } from "./public-client";
export type { PublicWorkspaceView } from "./public-client";
export { subscribeToEventStream } from "./sse";
export type { EventStream, EventStreamHandlers } from "./sse";

/**
 * The daemon's wire types, re-exported wholesale. Narrowing this list buys
 * nothing — `types.ts` is itself generated-adjacent, and every omission just
 * costs a consuming workstream a round trip.
 */
export type * from "./types";
