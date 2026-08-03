"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Maximize2,
  Minimize2,
  ChevronDown,
  GitCompare,
  Info,
  Plug,
  SlidersHorizontal,
  SquareTerminal,
  type LucideIcon,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { TerminalPane } from "@/components/terminal/terminal-pane";
import { StatTrio } from "@/components/workspace/stat-trio";
import { CommitList } from "@/components/workspace/commit-list";
import { PlacementBadge } from "@/components/workspace/placement-badge";
import { RuntimeBadge } from "@/components/workspace/runtime-badge";
import { PhaseMeter } from "@/components/workspace/phase-meter";
import { TicketLinkage } from "@/components/workspace/ticket-refs";
import { FleetTree } from "@/components/workspace/fleet-tree";
import { RelativeTime } from "@/components/shared/relative-time";
import { buildFleetTree, fleetMemberCount } from "@/lib/grove/fleet";
import { useInvokeControl, useSessionControls, useSwitchModel } from "@/lib/grove/hooks";
import { cn } from "@/lib/utils";
import type { AgentLiveStatus } from "@/lib/grove/agent-activity";
import type {
  CommitSummaryView,
  PhaseView,
  SessionActivityView,
  SessionControlView,
  WorkspacePeekView,
} from "@/lib/grove/types";

const SECTION_LABEL =
  "text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground";

type PanelTab = "terminal" | "diff" | "info" | "controls";

/**
 * The session page's work panel — the tabbed surface that fills
 * `AgentWorkspace`'s right pane. Three tabs, wire-data only:
 *
 * - **Terminal** — the existing `TerminalPane` machinery, verbatim (this is
 *   still Grove's D2 differentiator — a real tmux pane, not a builder-style
 *   activity log). Its tab carries a permanent live-pulse dot, the same
 *   convention `ViewSwitcher`'s terminal tab and the pane's own capture
 *   badge already use — one glyph language, not a second "unread" heuristic.
 * - **Diff** — ahead/behind/dirty (`StatTrio`) + the ± line-changed summary +
 *   the full `CommitList`. Still wire-data only (no hunk view — the daemon
 *   doesn't expose per-file diffs).
 * - **Info** — the workspace's whole identity read, in reading order:
 *   **Task** (the `PhaseMeter` — the task-phase axis in its expanded, read
 *   register: phase · step n/6 · note · when it was reported) · **Links**
 *   (`TicketLinkage` — the issue(s) and the PR, independently clickable) ·
 *   **Activity** (the metrics one-liner, `metrics` testid — the SAME seam the
 *   dashboard card uses, so a drift test can't miss it — plus subagent count,
 *   or the itemized fleet tree when the session's sub-agents are itemized on
 *   the wire) · **Identity** (agent + model, placement, runtime) ·
 *   **Timeline** (created/paused). Task and Links self-hide when the wire
 *   carries neither, so the tab is unchanged for a workspace with no phase and
 *   no refs. Deliberately omits `worktree_path` — a host filesystem path is
 *   host-private-ish and is never rendered anywhere in the UI.
 *
 * Tab selection is page-session-local `useState` (design ruling: "smallest
 * seam, don't add a store slice") — it resets on navigation, which is exactly
 * right for a per-visit UI preference. Full-screen is a plain CSS overlay
 * (`fixed inset-0`), not a portal/Dialog, so it composes with the resizable
 * split underneath without fighting focus-trap semantics.
 *
 * `sessions` (`WorkspaceActivityView.sessions`) is optional and defaults to
 * `[]` so every caller/test keeps working unchanged — it's the same flat
 * parent/child list the header identity popover and the session rail already
 * read off the activity snapshot, just threaded here too for the Info tab's
 * `FleetTree`. When it carries no itemized sub-agent (a plain single-session
 * workspace, or a kind that doesn't itemize its fleet), the tab falls back to
 * the bare `active_subagents` count line — never both, and never empty tree
 * chrome.
 *
 * - **Controls** — the session's input-control surface: the enumerated slash
 *   commands, skills, and configured MCP servers, plus the model catalog
 *   with a switch action. Read-only display is the core value; commands/skills
 *   carry a "Run" trigger and the model a switch, all thin best-effort verbs over
 *   the daemon's `/controls/*` routes. Degrades cleanly — an agent with no
 *   control surface (a shell/remote kind) renders a quiet empty state, no chrome.
 *
 * Test seams: `work-panel` (root), `work-panel-tab-terminal` / `-diff` / `-info`
 * / `-controls`, `work-panel-fullscreen`. The Terminal tab keeps every seam
 * `TerminalPane` already owns (`terminal-pane`, `terminal-capture-badge`,
 * `peek-snapshot`); the Controls tab owns `work-panel-controls-content`,
 * `controls-model-picker`, `controls-command`, `controls-skill`.
 */
export function WorkPanel({
  workspaceId,
  peek,
  live,
  commits,
  commitsLoading,
  sessions = [],
  phase = null,
  className,
}: {
  workspaceId: string;
  peek: WorkspacePeekView;
  live: AgentLiveStatus;
  commits: CommitSummaryView[] | undefined;
  commitsLoading?: boolean;
  /** The workspace's flat session list (primary + itemized fleet). */
  sessions?: SessionActivityView[];
  /** Task phase — rides `WorkspaceActivityView` (the SSE snapshot), not the
   *  peek, so it arrives from the same lookup `sessions` already comes from. */
  phase?: PhaseView | null;
  className?: string;
}) {
  const [tab, setTab] = useState<PanelTab>("terminal");
  const [fullscreen, setFullscreen] = useState(false);

  // Escape exits full screen — the standard overlay convention; harmless when
  // not full screen (the listener just never sees a reason to fire).
  useEffect(() => {
    if (!fullscreen) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setFullscreen(false);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [fullscreen]);

  return (
    <div
      data-testid="work-panel"
      className={cn(
        "flex min-h-0 min-w-0 flex-1 flex-col bg-background",
        fullscreen && "fixed inset-0 z-40",
        className,
      )}
    >
      <div className="flex shrink-0 items-center gap-0.5 bg-muted/40 px-2 py-1">
        <div role="tablist" aria-label="Work panel" className="flex items-center gap-0.5">
          <PanelTabButton
            testid="work-panel-tab-terminal"
            icon={SquareTerminal}
            label="Terminal"
            live
            selected={tab === "terminal"}
            onClick={() => setTab("terminal")}
          />
          <PanelTabButton
            testid="work-panel-tab-diff"
            icon={GitCompare}
            label="Diff"
            selected={tab === "diff"}
            onClick={() => setTab("diff")}
          />
          <PanelTabButton
            testid="work-panel-tab-info"
            icon={Info}
            label="Info"
            selected={tab === "info"}
            onClick={() => setTab("info")}
          />
          <PanelTabButton
            testid="work-panel-tab-controls"
            icon={SlidersHorizontal}
            label="Controls"
            selected={tab === "controls"}
            onClick={() => setTab("controls")}
          />
        </div>
        <button
          type="button"
          data-testid="work-panel-fullscreen"
          aria-label={fullscreen ? "Exit full screen" : "Full screen"}
          aria-pressed={fullscreen}
          onClick={() => setFullscreen((v) => !v)}
          className={cn(
            "ml-auto inline-flex size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:text-foreground",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background",
          )}
        >
          {fullscreen ? <Minimize2 className="size-3.5" /> : <Maximize2 className="size-3.5" />}
        </button>
      </div>

      {/* Only the active tab mounts (same conditional-mount contract the outer
          transcript/terminal single-pane view already uses) — the Terminal tab
          is stateless over its `peek` prop, so remounting on tab return costs
          nothing. */}
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        {tab === "terminal" && (
          <TerminalPane
            snapshot={peek.agent_snapshot}
            takenAt={peek.snapshot_taken_at}
            target={peek.state.tmux_session}
          />
        )}
        {tab === "diff" && <DiffTab peek={peek} commits={commits} commitsLoading={commitsLoading} />}
        {tab === "info" && (
          <InfoTab
            workspaceId={workspaceId}
            peek={peek}
            live={live}
            sessions={sessions}
            phase={phase}
          />
        )}
        {tab === "controls" && <ControlsTab workspaceId={workspaceId} />}
      </div>
    </div>
  );
}

function DiffTab({
  peek,
  commits,
  commitsLoading,
}: {
  peek: WorkspacePeekView;
  commits: CommitSummaryView[] | undefined;
  commitsLoading?: boolean;
}) {
  const changed = peek.diff_added > 0 || peek.diff_removed > 0;
  return (
    <div data-testid="work-panel-diff-content" className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-4">
      <StatTrio ahead={peek.base_ahead} behind={peek.base_behind} dirty={peek.dirty_files} />
      <p className="text-xs text-muted-foreground">
        {changed ? (
          <>
            <span className="font-medium text-[var(--ref-add)]">+{peek.diff_added}</span>
            {" / "}
            <span className="font-medium text-[var(--ref-remove)]">−{peek.diff_removed}</span>
            {" lines changed"}
          </>
        ) : (
          "Working tree clean."
        )}
      </p>
      <div className="min-h-0 flex-1">
        <CommitList commits={commits} isLoading={commitsLoading} />
      </div>
    </div>
  );
}

function InfoTab({
  workspaceId,
  peek,
  live,
  sessions,
  phase,
}: {
  workspaceId: string;
  peek: WorkspacePeekView;
  live: AgentLiveStatus;
  sessions: SessionActivityView[];
  phase: PhaseView | null;
}) {
  const s = peek.state;
  const ticketRefs = s.ticket_refs ?? [];
  // The itemized fleet wins over the bare count — a workspace whose
  // adapter/session doesn't itemize its sub-agents (or has none) falls back
  // to the plain one-liner; never both, never empty tree chrome.
  const fleetRoots = useMemo(() => buildFleetTree(sessions), [sessions]);
  const hasFleet = fleetMemberCount(fleetRoots) > 0;
  return (
    <div data-testid="work-panel-info-content" className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4">
      {/* Task and Links lead: they answer "what is this work and what is it
          for" — the questions Activity/Identity/Timeline only qualify. Both
          self-hide, so a workspace reporting neither reads exactly as before. */}
      {phase && (
        <section className="space-y-1.5">
          <p className={SECTION_LABEL}>Task</p>
          <PhaseMeter phase={phase} />
        </section>
      )}

      {ticketRefs.length > 0 && (
        <section className="space-y-1.5">
          <p className={SECTION_LABEL}>Links</p>
          <TicketLinkage refs={ticketRefs} className="flex-wrap" />
        </section>
      )}

      <section className="space-y-1.5">
        <p className={SECTION_LABEL}>Activity</p>
        <p data-testid="metrics" className="font-mono text-xs tabular-nums text-muted-foreground">
          {live.metricsLine ?? "—"}
        </p>
        {hasFleet ? (
          <FleetTree roots={fleetRoots} workspaceId={workspaceId} />
        ) : (
          live.subagents > 0 && (
            <p className="text-xs text-muted-foreground">
              {live.subagents} bg agent{live.subagents > 1 ? "s" : ""}
            </p>
          )
        )}
      </section>

      <section className="space-y-1.5">
        <p className={SECTION_LABEL}>Identity</p>
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge variant="outline" className="border-[var(--ref-info)]/40 font-mono text-[var(--ref-info)]">
            {s.agent_name}
          </Badge>
          {live.model && (
            <Badge variant="outline" className="font-mono">
              {live.model}
            </Badge>
          )}
          <PlacementBadge placement={s.placement} size="sm" />
          <RuntimeBadge
            runtime={s.runtime}
            runtimeFallbackReason={s.runtime_fallback_reason}
            runtimeDefaultConfig={s.runtime_default_config}
            size="sm"
          />
        </div>
      </section>

      <section className="space-y-1 text-xs text-muted-foreground">
        <p className={SECTION_LABEL}>Timeline</p>
        <p>
          Created <RelativeTime iso={s.created_at} />
        </p>
        {s.paused_at && (
          <p>
            Paused <RelativeTime iso={s.paused_at} />
          </p>
        )}
      </section>
    </div>
  );
}

/**
 * The Controls tab: the session's input-control surface. Read-only
 * enumeration is the core value — the model catalog (with a switch), the
 * slash commands, the skills, and the configured MCP servers. Commands and
 * skills carry a thin "Run" trigger (`/name` over the steer path); MCP servers
 * are informational. Every section is conditional, so an agent with no control
 * surface renders the quiet empty state, never empty chrome.
 */
function ControlsTab({ workspaceId }: { workspaceId: string }) {
  const { data, isLoading } = useSessionControls(workspaceId);
  const invoke = useInvokeControl(workspaceId);
  const switchModel = useSwitchModel(workspaceId);

  if (isLoading && !data) {
    return (
      <div data-testid="work-panel-controls-content" className="flex flex-col gap-3 p-4">
        <Skeleton className="h-6 w-40" />
        <Skeleton className="h-20 w-full" />
      </div>
    );
  }

  const controls = data;
  const hasAny =
    !!controls &&
    (controls.models.length > 0 ||
      controls.commands.length > 0 ||
      controls.skills.length > 0 ||
      controls.mcp_servers.length > 0 ||
      controls.permission_mode != null);

  if (!controls || !hasAny) {
    return (
      <div
        data-testid="work-panel-controls-content"
        className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 p-6 text-center"
      >
        <SlidersHorizontal aria-hidden className="size-6 text-muted-foreground" />
        <p className="text-sm text-muted-foreground">No session controls available.</p>
      </div>
    );
  }

  // A refusal (501 capability_unavailable / 409) is expected, not exceptional —
  // surface the daemon's typed message quietly. The mutations share one line.
  const failure = invoke.error ?? switchModel.error;
  const notice = failure instanceof Error ? failure.message : failure ? String(failure) : null;

  return (
    <div
      data-testid="work-panel-controls-content"
      className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4"
    >
      {controls.models.length > 0 && (
        <section className="space-y-1.5">
          <p className={SECTION_LABEL}>Model</p>
          <div className="flex flex-wrap items-center gap-2">
            <ModelSwitch
              current={controls.current_model}
              models={controls.models}
              pending={switchModel.isPending}
              onSwitch={(m) => switchModel.mutate(m)}
            />
            {controls.current_model && (
              <span className="text-xs text-muted-foreground">current</span>
            )}
          </div>
        </section>
      )}

      {controls.commands.length > 0 && (
        <ControlSection
          label="Commands"
          items={controls.commands}
          testid="controls-command"
          pending={invoke.isPending}
          onRun={(name) => invoke.mutate(name)}
        />
      )}

      {controls.skills.length > 0 && (
        <ControlSection
          label="Skills"
          items={controls.skills}
          testid="controls-skill"
          pending={invoke.isPending}
          onRun={(name) => invoke.mutate(name)}
        />
      )}

      {controls.mcp_servers.length > 0 && (
        <section className="space-y-1.5">
          <p className={SECTION_LABEL}>MCP servers</p>
          <div className="flex flex-wrap gap-1.5">
            {controls.mcp_servers.map((s) => (
              <Badge key={s.name} variant="outline" className="gap-1 font-mono text-xs">
                <Plug aria-hidden className="size-3" />
                {s.name}
              </Badge>
            ))}
          </div>
        </section>
      )}

      {controls.permission_mode && (
        <section className="space-y-1 text-xs text-muted-foreground">
          <p className={SECTION_LABEL}>Permission</p>
          <p>
            Prompt default: <span className="font-mono">{controls.permission_mode}</span>
          </p>
        </section>
      )}

      {notice && (
        <p role="status" className="text-xs text-[var(--status-error)]">
          {notice}
        </p>
      )}
    </div>
  );
}

/** One list of invokable controls (commands or skills) — each row is a name +
 *  optional description with a thin "Run" trigger delivering `/name`. */
function ControlSection({
  label,
  items,
  testid,
  pending,
  onRun,
}: {
  label: string;
  items: SessionControlView[];
  testid: string;
  pending: boolean;
  onRun: (name: string) => void;
}) {
  return (
    <section className="space-y-1.5">
      <p className={SECTION_LABEL}>{label}</p>
      <ul className="flex flex-col gap-0.5">
        {items.map((c) => (
          <li
            key={`${c.scope}:${c.name}`}
            data-testid={testid}
            className="flex items-center gap-2 rounded-md px-1.5 py-1 hover:bg-muted/60"
          >
            <div className="min-w-0 flex-1">
              <p className="truncate font-mono text-[13px] text-foreground">/{c.name}</p>
              {c.detail && (
                <p className="truncate text-xs text-muted-foreground">{c.detail}</p>
              )}
            </div>
            <Button
              variant="ghost"
              size="xs"
              disabled={pending}
              onClick={() => onRun(c.name)}
              aria-label={`Run ${c.name}`}
            >
              Run
            </Button>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** The model switcher — a `Model ▾` dropdown over the offered catalog, mirroring
 *  the composer's `ModelPicker` anatomy (no bespoke chrome). Selecting an id
 *  delivers the `/model <id>` control; the current model is marked. */
function ModelSwitch({
  current,
  models,
  pending,
  onSwitch,
}: {
  current: string | null;
  models: string[];
  pending: boolean;
  onSwitch: (model: string) => void;
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          data-testid="controls-model-picker"
          variant="outline"
          size="sm"
          disabled={pending}
          className="gap-1 font-mono"
          aria-label={`Model: ${current ?? "unknown"}`}
        >
          <span className="truncate">{current ?? "Switch model"}</span>
          <ChevronDown aria-hidden className="size-3.5 opacity-60" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-52">
        <DropdownMenuLabel>Switch model</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {models.map((id) => (
          <DropdownMenuItem key={id} className="font-mono" onSelect={() => onSwitch(id)}>
            {id}
            {current === id && (
              <span className="ml-auto text-xs text-muted-foreground">current</span>
            )}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** A quiet internal tab, same visual language as `ViewSwitcher`'s TabButton
 *  (text at `sm`+, icon-only below it) so the panel's own tab bar doesn't
 *  introduce a second tab idiom. */
function PanelTabButton({
  selected,
  onClick,
  testid,
  icon: Icon,
  label,
  live,
}: {
  selected: boolean;
  onClick: () => void;
  testid: string;
  icon: LucideIcon;
  label: string;
  live?: boolean;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={selected}
      aria-label={label}
      data-testid={testid}
      onClick={onClick}
      className={cn(
        "inline-flex h-6 items-center gap-1 rounded-md px-2 text-xs transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background",
        selected ? "bg-accent font-medium text-foreground" : "text-muted-foreground hover:text-foreground",
      )}
    >
      <Icon aria-hidden className="size-3.5 shrink-0 sm:hidden" />
      <span className="hidden sm:inline">{label}</span>
      {live && (
        <span
          aria-hidden
          className="size-1.5 shrink-0 rounded-full bg-[var(--status-active)] motion-safe:animate-pulse"
        />
      )}
    </button>
  );
}
