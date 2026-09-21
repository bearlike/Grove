export { base64FromBytes, GroveClient, GroveProtocolError } from "./client";
export type { WorkspacePanelView } from "./panels";
export type { SubagentFleetData, SubagentFleetMember, SubagentFleetSession } from "./fleet";
export { diagramOf } from "./diagrams";
export type {
  DiagramDocumentView,
  DiagramMode,
  DiagramOpenRequest,
  DiagramPreviewUploadRequest,
  DiagramPreviewView,
  DiagramSessionView,
  DiagramStopRequest,
  DiagramUpdateRequest,
  DiagramWriter,
} from "./diagrams";
export { PublicClient, publicClient } from "./public-client";
export type { PublicWorkspaceView } from "./public-client";
export { subscribeToEventStream } from "./sse";
export type { EventStream, EventStreamHandlers, GroveStreamEvent } from "./sse";

/**
 * The daemon's wire types, re-exported wholesale. Narrowing this list buys
 * nothing — `types.ts` is itself generated-adjacent, and every omission just
 * costs a consuming workstream a round trip.
 */
export type * from "./types";
