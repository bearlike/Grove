"use client";
import { Activity, Github, Server } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useDaemonWhoami, useWorkspaces } from "@/lib/grove/hooks";

const REPO_URL = "https://github.com/bearlike/Grove";

/**
 * Compact uptime renderer: at most two units, largest non-zero first.
 *
 * Examples: 5 -> "5s", 65 -> "1m 5s", 3700 -> "1h 1m", 90061 -> "1d 1h",
 * 0 / negative -> "0s". Two-unit cap keeps the status-bar strip tight.
 */
export function formatUptime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "0s";
  const s = Math.floor(seconds);
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (d > 0) return h > 0 ? `${d}d ${h}h` : `${d}d`;
  if (h > 0) return m > 0 ? `${h}h ${m}m` : `${h}h`;
  if (m > 0) return sec > 0 ? `${m}m ${sec}s` : `${m}m`;
  return `${sec}s`;
}

/**
 * Always-visible bottom strip, modeled on VS Code's status bar:
 * persistent app context (daemon health, workspace count, branch help)
 * sitting on a flat tinted band. Information density over chrome —
 * tiny text, single horizontal line, no card frames. The whole strip
 * is at the viewport bottom and never scrolls.
 *
 * Daemon state derives from two queries: ``useWorkspaces()`` for the
 * count + reachability fallback, and ``useDaemonWhoami()`` for the
 * version/uptime/host triple. Both are background-polled by TanStack
 * Query; the local 1-second tick interpolates uptime seconds between
 * whoami refetches so the displayed age stays smooth.
 */
export function StatusBar() {
  const { data: workspaces, isError: workspacesError } = useWorkspaces();
  const { data: whoami, isError: whoamiError } = useDaemonWhoami();
  const count = workspaces?.length ?? 0;

  // Local clock that ticks every second so "up Xm Ys" advances between
  // 30 s whoami refetches. Re-anchored on each fetch via the dependency
  // on whoami.uptime_seconds + whoami.started_at.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const isReachable = !workspacesError && !whoamiError;
  // Compute live uptime from started_at on every render so the displayed
  // value advances each tick of the 1 s setInterval above. Subtracts
  // wall-clock-now from started_at, which is correct as long as host and
  // browser clocks are roughly aligned (a few-second skew shows up as a
  // small offset, not as a runaway drift).
  const liveUptime = whoami
    ? Math.max(0, Math.floor((now - new Date(whoami.started_at).getTime()) / 1000))
    : 0;

  const tooltipText = whoami
    ? `${whoami.user}@${whoami.host} · ${whoami.platform} · python ${whoami.python_version}`
    : "daemon details unavailable";

  return (
    <footer
      role="contentinfo"
      className="fixed inset-x-0 bottom-0 z-30 border-t border-border bg-card/90 text-[11px] backdrop-blur supports-[backdrop-filter]:bg-card/70"
    >
      <div className="mx-auto flex h-7 max-w-screen-xl items-center gap-3 px-3 text-muted-foreground">
        <Tooltip>
          <TooltipTrigger asChild>
            <span
              className="inline-flex cursor-default items-center gap-1.5"
              data-testid="daemon-status"
            >
              <Server className="size-3" aria-hidden />
              <span>daemon</span>
              <span
                aria-hidden
                className="inline-block h-1.5 w-1.5 rounded-full"
                style={{
                  backgroundColor: isReachable
                    ? "var(--status-active)"
                    : "var(--status-error)",
                }}
              />
              <span className="font-medium text-foreground">
                {isReachable ? "online" : "unreachable"}
              </span>
              {whoami && (
                <>
                  <span className="text-muted-foreground/60">·</span>
                  <span className="font-mono text-[10px]" data-testid="daemon-version">
                    v{whoami.version}
                  </span>
                  <span className="text-muted-foreground/60">·</span>
                  <span data-testid="daemon-uptime">up {formatUptime(liveUptime)}</span>
                </>
              )}
            </span>
          </TooltipTrigger>
          <TooltipContent side="top" align="start">
            <span data-testid="daemon-tooltip">{tooltipText}</span>
          </TooltipContent>
        </Tooltip>
        <span className="hidden sm:inline-flex items-center gap-1.5">
          <Activity className="size-3" aria-hidden />
          <span>{count} {count === 1 ? "workspace" : "workspaces"}</span>
        </span>
        <span className="ml-auto inline-flex items-center gap-3">
          <span className="hidden sm:inline">read-only dashboard</span>
          <Link
            href={REPO_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 rounded-sm px-1 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
          >
            <Github className="size-3" aria-hidden />
            <span>github</span>
          </Link>
        </span>
      </div>
    </footer>
  );
}
