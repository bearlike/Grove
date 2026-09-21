"use client";

import { createContext, useContext, type PropsWithChildren, type ReactNode } from "react";
import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import { groveClient } from "./client";
import { groveKeys } from "./keys";
import { publicKeys } from "./public";
import { publicClient } from "../api/public-client";
import type { ToolCallView } from "@/lib/grove/api";

/**
 * Fetching ONE tool body the windowed transcript withheld.
 *
 * A windowed `/turns` read carries every settled call's identity and status and
 * drops its request/result outside the tail turn, marking them
 * `body: "available"` (the daemon's head + drill-in pairing). A historical tool
 * step mounts COLLAPSED and Radix unmounts a closed `CollapsibleContent`, so
 * the body was never in the DOM anyway — this is what puts it there on the one
 * interaction that asks for it.
 *
 * The context exists because the renderer cannot know its own coordinate. A
 * `ToolCallPart` is mounted by assistant-ui from a message part, three
 * providers deep, and the same component draws a workspace transcript, a public
 * share and the catalog's archived session. Threading `workspaceId` through
 * assistant-ui's own props is not possible; a context at the Thread mount is.
 */
export type ToolBodySource =
  /** An authenticated workspace transcript: the drill-in takes both coordinates. */
  | { kind: "workspace"; workspaceId: string; sessionId: string }
  /** A public share: the TOKEN names the session, so there is no coordinate. */
  | { kind: "public"; token: string };

/**
 * `null` means "this surface serves no drill-in", which is a real answer rather
 * than a missing provider: the catalog's archived-session route reads
 * `/sessions/{id}/turns`, which withholds nothing, so every body it renders is
 * already inline and a fetch affordance there would be dead chrome.
 */
const ToolBodyContext = createContext<ToolBodySource | null>(null);

export function ToolBodyProvider({
  source,
  children,
}: PropsWithChildren<{ source: ToolBodySource | null }>): ReactNode {
  return <ToolBodyContext.Provider value={source}>{children}</ToolBodyContext.Provider>;
}

export function useToolBodySource(): ToolBodySource | null {
  return useContext(ToolBodyContext);
}

/**
 * The withheld body for one call, fetched only once the reader OPENS it.
 *
 * `enabled` is `shouldFetchToolBody`'s answer — the pure predicate that owns
 * the gate — so a collapsed step issues no request and an `inline` or `none`
 * body issues none ever. The query is keyed by the CALL rather than by the
 * transcript: a settled body is immutable (it is only fetchable because it
 * settled), so `staleTime: Infinity` is a statement about the data, not a
 * caching guess, and the answer survives every poll that rewrites the turns.
 */
export function useToolBody(
  toolUseId: string,
  enabled: boolean,
): UseQueryResult<ToolCallView> {
  const source = useToolBodySource();
  return useQuery({
    queryKey:
      source?.kind === "public"
        ? publicKeys.tool(source.token, toolUseId)
        : groveKeys.tool(
            source?.kind === "workspace" ? source.workspaceId : "",
            source?.kind === "workspace" ? source.sessionId : "",
            toolUseId,
          ),
    queryFn: () =>
      source?.kind === "public"
        ? publicClient.tool(source.token, toolUseId)
        : groveClient.getSessionTool(
            (source as { workspaceId: string }).workspaceId,
            (source as { sessionId: string }).sessionId,
            toolUseId,
          ),
    enabled: enabled && source !== null && toolUseId.length > 0,
    staleTime: Infinity,
    gcTime: Infinity,
  });
}
