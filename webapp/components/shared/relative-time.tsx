"use client";
import { useEffect, useState } from "react";

export function RelativeTime({ iso }: { iso: string | null }) {
  const [, setTick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), 30_000);
    return () => clearInterval(t);
  }, []);
  if (!iso) return <span className="text-muted-foreground">—</span>;
  return <span title={iso}>{relativeTimeLabel(iso)}</span>;
}

/** The relative-time text ("5s ago" / "3h ago" / "2d ago"), shared with any
 *  surface that needs the string form — e.g. a rail row's native `title`
 *  tooltip where the time can no longer ride a visible `<RelativeTime>`. */
export function relativeTimeLabel(iso: string): string {
  const then = new Date(iso).getTime();
  const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}
