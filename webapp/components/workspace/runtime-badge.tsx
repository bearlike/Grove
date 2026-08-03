import { TriangleAlert } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { runtimeGlyph, runtimeLabel } from "@/lib/grove/runtime-tokens";
import type { Runtime } from "@/lib/grove/types";

/**
 * Runtime tag — the labeled register of the isolation axis, for the identity
 * surfaces that have room for a word (work-panel Info, the session popover).
 * The dense surfaces use the bare `RuntimeMark` instead.
 *
 * **Both runtimes are marked.** This used to open with
 * `if (runtime !== "container") return null`, mirroring `PlacementBadge`'s
 * "silence is the signal" philosophy — and that was the wrong rule for this one
 * axis. Placement's default is a boring implementation detail; runtime is the
 * isolation boundary (whether the agent can reach the host filesystem, host
 * network and the user's credentials), so a host workspace rendering NOTHING
 * left "runs on your machine" indistinguishable from "this component didn't
 * render". Silence stays correct for `PlacementBadge`; do not restore it here.
 *
 * The glyph is the TUI's own character (`grove.core.contracts.runtime_palette`
 * via `runtime-tokens`), not a lucide icon — one vocabulary across both
 * clients, enforced by a drift test, exactly as `AgentStateMark` already does
 * for agent state.
 *
 * Three tones, and the degradations must stay distinct from the plain marks:
 *
 * - **Fallback** (`runtimeFallbackReason` set) — a PERSISTENT warning. The
 *   workspace wanted a container and got host instead; its isolation contract
 *   was voided for its whole lifetime. Amber, `TriangleAlert`, title names the
 *   reason + the `respawn` remedy. Never folded into the plain host mark.
 * - **Default container** (`runtime === "container" && runtimeDefaultConfig`)
 *   — informational, NOT a degradation: the repo has no `.devcontainer/`, so
 *   this workspace ran Grove's packaged default image. Title names
 *   `grove init devcontainer` as the graduation path.
 * - **Plain host / plain container** — the quiet permanent mark, tinted with
 *   the axis hue (`--runtime-*`).
 *
 * Test seam: `data-testid="runtime-badge"` + `data-runtime`/`data-fallback`.
 */
export function RuntimeBadge({
  runtime,
  runtimeFallbackReason,
  runtimeDefaultConfig,
  size = "md",
  className,
}: {
  runtime: Runtime;
  runtimeFallbackReason?: string | null;
  runtimeDefaultConfig?: boolean;
  size?: "sm" | "md";
  className?: string;
}) {
  const sizing = size === "sm" ? "px-1.5 py-0 text-[10px]" : "px-2 text-[10px]";
  const base = "gap-1 rounded-full font-medium uppercase tracking-[0.08em]";

  if (runtimeFallbackReason) {
    return (
      <Badge
        variant="outline"
        data-runtime={runtime}
        data-fallback="true"
        data-testid="runtime-badge"
        title={`Container unavailable: ${runtimeFallbackReason} — fix the runtime, then respawn to promote`}
        className={cn(
          // No dedicated `--status-warning` token — `--status-orphaned` IS
          // Grove's amber "attention, not failure" hue (see tui/CLAUDE.md's
          // "PAUSED = neutral gray, STALE/ORPHANED = amber" rule); reused here
          // rather than minting a new semantic color for the same meaning.
          base,
          "border-[var(--status-orphaned)]/40 bg-[var(--status-orphaned)]/10 text-[var(--status-orphaned)]",
          sizing,
          className,
        )}
      >
        <TriangleAlert aria-hidden className="size-3 shrink-0" />
        <span>fallback</span>
      </Badge>
    );
  }

  return (
    <Badge
      variant="outline"
      data-runtime={runtime}
      data-testid="runtime-badge"
      title={
        runtimeDefaultConfig && runtime === "container"
          ? "Grove's default container — run `grove init devcontainer` to graduate to a committed config"
          : runtime === "container"
            ? "Runs in the project's devcontainer"
            : "Runs directly on this host — shares your filesystem, network and credentials"
      }
      className={cn(base, "border-transparent bg-muted/40 text-muted-foreground", sizing, className)}
    >
      <span
        aria-hidden
        className="font-mono leading-none"
        style={{ color: `var(--runtime-${runtime}, var(--runtime-host))` }}
      >
        {runtimeGlyph(runtime)}
      </span>
      <span>{runtimeLabel(runtime)}</span>
    </Badge>
  );
}
