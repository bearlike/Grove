"use client";

import { useEffect, useState } from "react";

import { GroveClient, subscribeToEventStream } from "@/lib/grove/api";
import type { LivePulseEvent, LivePulseView } from "@/lib/grove/api";

/**
 * What a native session is doing this second, while the caller is mounted AND
 * the tab is visible — and not one frame otherwise.
 *
 * Opening the stream is what makes the daemon ask the worker to publish at
 * all (the first viewer turns the watch on, the last one off), so this hook's
 * lifetime IS the feature's cost. The working loader mounts it only while the
 * agent works; a hidden tab closes it rather than holding a watch open for a
 * reader who is not looking. Nothing is cached: the next mount starts fresh,
 * because a step from a minute ago is not what the session is doing now.
 */
export function useLivePulse(workspaceId: string | null): LivePulseView | null {
  const [pulse, setPulse] = useState<LivePulseView | null>(null);
  const visible = useDocumentVisible();

  useEffect(() => {
    setPulse(null);
    if (!workspaceId || !visible) return undefined;
    const stream = subscribeToEventStream<LivePulseEvent>(GroveClient.liveStreamUrl(workspaceId), {
      onEvent: (event) => {
        // The shared listener also delivers `heartbeat` frames (`{}`); only a
        // frame that states a step field is a pulse.
        if (event && "step" in event) setPulse(event);
      },
    });
    return () => stream?.close();
  }, [workspaceId, visible]);

  return pulse;
}

function useDocumentVisible(): boolean {
  const [visible, setVisible] = useState(
    () => typeof document === "undefined" || !document.hidden,
  );
  useEffect(() => {
    const sync = (): void => setVisible(!document.hidden);
    document.addEventListener("visibilitychange", sync);
    return () => document.removeEventListener("visibilitychange", sync);
  }, []);
  return visible;
}
