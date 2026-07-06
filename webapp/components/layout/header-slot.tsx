"use client";

import { createContext, useContext } from "react";

/**
 * The app header now lives in the shared shell layout (one header for every
 * route), so a page that needs to fill the header's middle — today only the
 * session page (`/w/[id]`) with its identity + view cluster — can no longer pass
 * a `context` prop up to it. Instead the layout captures the header's middle DOM
 * node and publishes it here; the page portals its cluster into it with
 * `createPortal`.
 *
 * A portal (not a React node threaded through state/store) is deliberate: the
 * portaled subtree re-renders WITH the page, so live session data (peek, agent
 * state, commits) stays fresh without pushing an ever-changing node through
 * React state — which would loop, since the cluster is a fresh node each render.
 * The value is null until the header commits its ref (one tick after mount) and
 * on every route that publishes nothing, where the header middle is a plain
 * `flex-1` spacer.
 */
export const HeaderSlotContext = createContext<HTMLElement | null>(null);

/** The header's middle DOM node to portal a per-route cluster into, or null. */
export function useHeaderSlot(): HTMLElement | null {
  return useContext(HeaderSlotContext);
}
