import { AgentGlyph } from "@/lib/grove/agent-icon";
import { tierForActivity } from "@/lib/grove/activity-tier";
import { cn } from "@/lib/utils";
import type { AgentActivityState, WorkspaceStatus } from "@/lib/grove/types";

/**
 * The agent badge that sits next to a card's title/profile: "which agent, and
 * is it running?" — a single component the card agent drops in. It composes the
 * resolved AgentGlyph with the activity tier's treatment (the policy lives in
 * `tierForActivity`, never re-derived here):
 *
 *   - active    → static glyph wrapped in an emerald `animate-ping` pulsing
 *                 ring. The glyph stays still and legible (spinning a brand mark
 *                 reads as broken); only the ring animates. `motion-reduce`
 *                 users get the static ring.
 *   - attention → static glyph + a small accent ring/dot in the amber/red tier
 *                 accent. Full opacity — attention must pop.
 *   - dormant   → static glyph at the tier's reduced opacity, no animation.
 */
export function AgentBadge({
  agentName,
  adapterKind,
  primaryState,
  workspaceStatus,
  className,
}: {
  agentName: string;
  adapterKind?: string;
  primaryState: AgentActivityState | null;
  workspaceStatus: WorkspaceStatus;
  className?: string;
}) {
  const { tier, opacityClass, accentVar } = tierForActivity(primaryState, workspaceStatus);

  return (
    <span
      className={cn("relative inline-flex size-6 items-center justify-center", opacityClass, className)}
      style={{ color: accentVar }}
    >
      {tier === "active" && (
        // Emerald pulse — the running cue. Static for reduced-motion users.
        <span
          aria-hidden
          className="absolute inset-0 rounded-full bg-[var(--agent-working)]/30 motion-safe:animate-ping motion-reduce:opacity-40"
        />
      )}
      {tier === "attention" && (
        // Static accent ring — highlights without animating.
        <span
          aria-hidden
          className="absolute inset-0 rounded-full ring-2"
          style={{ color: accentVar }}
        />
      )}
      <AgentGlyph agentName={agentName} adapterKind={adapterKind} className="relative size-4" />
    </span>
  );
}
