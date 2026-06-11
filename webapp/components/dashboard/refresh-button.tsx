"use client";

import { useEffect, useRef, useState } from "react";
import { RotateCw } from "lucide-react";
import { Button } from "@/components/ui/button";

/**
 * Force a fresh pull of the whole dashboard. `onRefresh` tears down and
 * reconnects the SSE stream (the daemon resends a fresh `snapshot`) and resets
 * the poll fallback — see `useActivityStream().refresh`. The icon spins briefly
 * on click as feedback; the stream itself is the source of truth for liveness
 * (the "updated …" indicator), so the spin is a short fixed flourish rather
 * than a request-bound spinner. `animate-spin` honors reduced-motion via the
 * `motion-reduce:animate-none` contract.
 */
export function RefreshButton({ onRefresh }: { onRefresh: () => void }) {
  const [spinning, setSpinning] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  const handle = () => {
    onRefresh();
    setSpinning(true);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setSpinning(false), 600);
  };

  return (
    <Button
      type="button"
      variant="ghost"
      size="icon-sm"
      onClick={handle}
      aria-label="Refresh dashboard"
      data-testid="dashboard-refresh"
    >
      <RotateCw
        aria-hidden
        className={spinning ? "animate-spin motion-reduce:animate-none" : undefined}
      />
    </Button>
  );
}
