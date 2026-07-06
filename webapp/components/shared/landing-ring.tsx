"use client";

import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";

// How recently a workspace/session must have been created for its card/row to
// ring as it lands. Comfortably covers the rail's 15 s history poll so a
// just-created session still rings when its row first appears; an older entry
// (or a plain page reload of long-lived work) never does.
const LANDING_WINDOW_MS = 15_000;
// One decay pass, then the overlay unmounts and leaves no trace.
const RING_DURATION_MS = 900;

/**
 * The one-shot "just landed" ring (design-direction.md §4f / brand.md §4.6 — the
 * SECOND sanctioned TEMPORAL terracotta use, after the skeleton shimmer). A
 * freshly-created workspace card or session row flashes a terracotta ring as it
 * appears, then it decays to nothing — it must NEVER persist (a steady-state
 * terracotta fill would break accent scarcity).
 *
 * Rendered as an absolutely-positioned, `pointer-events-none` overlay SIBLING so
 * it never touches the host's own `className` — the card/row `ring-*`/`border-*`
 * contracts (and their tests) stay intact, and clicks pass straight through to
 * the row link beneath. The host must be `relative`.
 *
 * Fires once on mount, only when `landedAt` is within LANDING_WINDOW_MS of now.
 * motion-safe fades the inset ring out over the pass (`grove-ring`, fill-forwards
 * so it ends transparent); under `prefers-reduced-motion` the ring shows
 * statically for the window then the overlay unmounts — a flash, no interpolation.
 * Either way it is gone within ~1s.
 */
export function LandingRing({
  landedAt,
  className,
}: {
  landedAt: string | null;
  className?: string;
}) {
  const [show, setShow] = useState(false);

  useEffect(() => {
    if (!landedAt) return;
    const t = Date.parse(landedAt);
    if (Number.isNaN(t) || Date.now() - t > LANDING_WINDOW_MS) return;
    setShow(true);
    const id = window.setTimeout(() => setShow(false), RING_DURATION_MS);
    return () => window.clearTimeout(id);
  }, [landedAt]);

  if (!show) return null;
  return (
    <span
      aria-hidden
      data-testid="landing-ring"
      className={cn(
        "pointer-events-none absolute inset-0 z-10 rounded-[inherit]",
        "[box-shadow:inset_0_0_0_2px_hsl(var(--primary)/0.55)] motion-safe:animate-grove-ring",
        className,
      )}
    />
  );
}
