import type {
  DiagramDocumentView,
  DiagramSessionView,
  DiagramStopRequest,
  DiagramUpdateRequest,
} from "./types";

export type {
  DiagramDocumentView,
  DiagramOpenRequest,
  DiagramSessionView,
  DiagramStopRequest,
  DiagramUpdateRequest,
} from "./types";

export type DiagramMode = DiagramSessionView["mode"];

export type { DiagramPreviewUploadRequest, DiagramPreviewView } from "./types";

/** The persistence controller accepts a real client or an in-memory test writer. */
export interface DiagramWriter {
  update(request: DiagramUpdateRequest): Promise<DiagramDocumentView>;
  stop(request: DiagramStopRequest): Promise<DiagramDocumentView>;
}

/** Shared work-panel compositions also receive public records without this field. */
export function diagramOf(state: unknown): DiagramSessionView | null {
  if (typeof state !== "object" || state === null) return null;
  const value = (state as { diagram?: unknown }).diagram;
  if (typeof value !== "object" || value === null) return null;
  const candidate = value as Partial<DiagramSessionView>;
  return typeof candidate.path === "string" &&
    typeof candidate.session_id === "string" &&
    (candidate.mode === "active" || candidate.mode === "read_only")
    ? {
        path: candidate.path,
        session_id: candidate.session_id,
        mode: candidate.mode,
      }
    : null;
}
