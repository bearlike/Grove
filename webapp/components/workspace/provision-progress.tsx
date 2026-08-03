"use client";

import { useEffect, useState } from "react";
import { ChevronDownIcon } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/workspace/status-badge";
import { TerminalView } from "@/components/terminal/terminal-view";
import { useProvisionProgress } from "@/lib/grove/hooks";
import { elapsedLabel } from "@/lib/grove/format";
import { cn } from "@/lib/utils";

/**
 * The two surfaces a workspace shows while its container is being built.
 *
 * The defect these exist to end: a container workspace is persisted the moment
 * `create` starts and its container does not exist for 49 s (warm) to 6.5 min
 * (cold image build) afterwards. That whole window used to read OFFLINE — grey,
 * still, and advertising `respawn`, the one verb that destroys the build in
 * flight. So the bar here is not decoration: the user must be able to see, at a
 * glance, that work is happening, roughly how long it has been happening, and
 * what it is doing right now.
 *
 * Three signals, each answering a question the others cannot (the same triple
 * the engine's `ProvisionProgress` carries):
 *   - an indeterminate bar — *is this alive*. It is the app's ONE loading
 *     primitive, `Skeleton` (the sanctioned `grove-shimmer` sweep, motion-safe,
 *     static under reduced motion), not a spinner: a build has no percentage,
 *     and inventing one from someone else's log format would report confidently
 *     wrong numbers.
 *   - the elapsed clock — *has this been going long enough to worry*. Counted
 *     locally from `provision_started_at` so it ticks every second between the
 *     2 s polls, and never reads as frozen when a request is slow.
 *   - the headline — *is it moving*, which no timer can answer. The last line
 *     the provisioner wrote, verbatim.
 *
 * The log tail sits behind a fold and renders through `TerminalView`, the same
 * ANSI-aware `<pre>` the agent pane uses — a BuildKit log is terminal output,
 * and it sticks to the bottom as new lines arrive for free.
 */

/**
 * Elapsed milliseconds, ticking locally once a second.
 *
 * Prefers the client-side clock over the daemon's `elapsed_ms` for the reason
 * `WorkspaceStateView.provision_started_at` exists: a server-computed elapsed is
 * stale the instant it leaves the daemon, and between two 2 s polls it would sit
 * visibly frozen — exactly the "nothing is happening" reading this whole feature
 * removes. The server value is the fallback for a workspace whose start stamp
 * never made it onto the record.
 */
function useElapsedMs(startedAt: string | null | undefined, serverMs: number | null): number | null {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1_000);
    return () => clearInterval(t);
  }, []);
  if (!startedAt) return serverMs;
  const started = new Date(startedAt).getTime();
  if (Number.isNaN(started)) return serverMs;
  return Math.max(0, now - started);
}

/**
 * The grid card's provisioning slot — it REPLACES the "happening now" line,
 * which would otherwise read "no agent session" for the entire build (true, and
 * the single most misleading thing the card could say while a container is
 * being assembled).
 *
 * One line plus a hairline bar: the card is a glance surface, so the tail lines
 * live only on the detail page. `data-testid="provision-line"`.
 */
export function ProvisionLine({
  workspaceId,
  startedAt,
}: {
  workspaceId: string;
  startedAt: string | null | undefined;
}) {
  const { data } = useProvisionProgress(workspaceId);
  const elapsed = useElapsedMs(startedAt, data?.elapsed_ms ?? null);
  const headline = data?.headline?.trim() || null;

  return (
    <div data-testid="provision-line" className="flex flex-col gap-1">
      <p className="flex items-center gap-2 text-[13px] leading-snug">
        <span className="shrink-0 font-medium text-[var(--status-provisioning)]">
          Building container
        </span>
        {elapsed !== null && (
          <span data-testid="provision-elapsed" className="shrink-0 tabular-nums text-muted-foreground">
            {elapsedLabel(elapsed)}
          </span>
        )}
        <span
          aria-live="polite"
          className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted-foreground"
          title={headline ?? undefined}
        >
          {headline ?? "starting up…"}
        </span>
      </p>
      {/* Indeterminate, by design — see the file header. */}
      <Skeleton className="h-0.5 w-full rounded-full" />
    </div>
  );
}

/**
 * The session page's provisioning banner, mounted above the transcript/terminal
 * panes while the container is coming up. Those panes have nothing to show yet
 * (there is no agent, because there is no container), so this is the page's
 * primary content for the length of the build — hence the fold with the real
 * build log behind it: a user who does not believe the headline can watch the
 * thing itself.
 *
 * The "several minutes" line is deliberate copy, not filler. A 6.5-minute cold
 * build with no stated expectation is indistinguishable from a hang, and the
 * user's response to a hang is a destructive verb.
 *
 * `data-testid="provision-panel"`; the fold's log is `provision-log`.
 */
export function ProvisionPanel({
  workspaceId,
  startedAt,
  className,
}: {
  workspaceId: string;
  startedAt: string | null | undefined;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const { data } = useProvisionProgress(workspaceId);
  const elapsed = useElapsedMs(startedAt, data?.elapsed_ms ?? null);
  const headline = data?.headline?.trim() || null;
  const lines = data?.lines ?? [];

  return (
    <section
      data-testid="provision-panel"
      aria-label="Container provisioning"
      className={cn("rounded-lg border border-border/60 bg-card p-3", className)}
    >
      <div className="flex items-center gap-2">
        <StatusBadge status="provisioning" size="sm" />
        <span className="min-w-0 flex-1 truncate text-sm font-medium">
          Building this workspace&rsquo;s container
        </span>
        {elapsed !== null && (
          <span
            data-testid="provision-elapsed"
            className="shrink-0 tabular-nums text-xs text-muted-foreground"
          >
            {elapsedLabel(elapsed)}
          </span>
        )}
      </div>

      <Skeleton className="mt-2.5 h-1 w-full rounded-full" />

      <p
        data-testid="provision-headline"
        aria-live="polite"
        className="mt-2 truncate font-mono text-[13px] text-muted-foreground"
        title={headline ?? undefined}
      >
        {headline ?? "Waiting for the provisioner to report…"}
      </p>
      <p className="mt-1 text-xs text-muted-foreground/70">
        A cold image build can take several minutes. The agent starts on its own
        as soon as the container is up.
      </p>

      {/* The fold. Plain local state + a conditional mount and the
          `-rotate-90`-when-closed chevron — the same disclosure grammar the
          transcript's tool group and todo list already use, so the app has one
          expander idiom rather than three. */}
      <button
        type="button"
        aria-expanded={open}
        data-testid="provision-log-toggle"
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "mt-2 flex w-fit items-center gap-2 rounded-md py-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
        )}
      >
        <span>Build log</span>
        <ChevronDownIcon
          aria-hidden
          className={cn("size-4 shrink-0 transition-transform", !open && "-rotate-90")}
        />
      </button>
      {open && (
        <TerminalView
          ansi={lines.length > 0 ? lines.join("\n") : null}
          textTestId="provision-log"
          ariaLabel="Container build log"
          emptyLabel="No build output yet."
          className="mt-1 max-h-64 rounded-md border border-border bg-background"
        />
      )}
    </section>
  );
}
