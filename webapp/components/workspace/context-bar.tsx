"use client";

import { useState, type ReactNode } from "react";
import { ChevronDown, LoaderCircle, Pause, Play, RotateCcw, Trash2 } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { AgentStateMark } from "@/components/shared/state-mark";
import { StatusBadge } from "@/components/workspace/status-badge";
import { PlacementBadge } from "@/components/workspace/placement-badge";
import { RuntimeBadge } from "@/components/workspace/runtime-badge";
import { PhaseBadge } from "@/components/workspace/phase-badge";
import { PhaseMeter } from "@/components/workspace/phase-meter";
import { TicketLinkage } from "@/components/workspace/ticket-refs";
import { StatTrio } from "@/components/workspace/stat-trio";
import { KillConfirmDialog } from "@/components/workspace/kill-confirm-dialog";
import { useWorkspaceActions } from "@/lib/grove/hooks";
import { availableActions } from "@/lib/grove/workspace-actions";
import { GroveProtocolError } from "@/lib/grove/client";
import { agentStateLabel } from "@/lib/grove/agent-state-tokens";
import { statusLabel } from "@/lib/grove/status-tokens";
import { cn } from "@/lib/utils";
import type { AgentLiveStatus } from "@/lib/grove/agent-activity";
import type { PhaseView, WorkspacePeekView } from "@/lib/grove/types";

const SECTION_LABEL =
  "text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground";

// One shared shape for a full-width popover action row (Actions + Danger zone).
const ACTION_ROW = cn(
  "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm font-medium",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
  "disabled:pointer-events-none disabled:opacity-50",
);

/**
 * The session page's ONE identity + control surface: a state-led title
 * trigger that opens a single popover. Everything the header cluster needs
 * at a glance lives one click behind the title — the calm-chrome default —
 * rather than spread flat across the header at every width.
 *
 * Trigger (`identity-trigger`): a leading state mark (SIBLING of the button —
 * `AgentStateMark`/`StatusBadge` is block-ish, so the button's phrasing content
 * stays the heading + chevron) + the truncated title + a short inline label kept
 * ONLY for blocked/error (color is never the sole signal) + the amber
 * `branch-delta-dot` (`var(--status-orphaned)`, ridden only when the branch has
 * any ahead/behind/dirty delta — the glanceable "something to pull" cue) +
 * `ChevronDown`. The live state word rides an sr-only `aria-live` region beside
 * the mark so a screen reader hears the state FLIP without the raw task/prompt
 * text ever being announced.
 *
 * All THREE status axes are legible here, each in its own register: lifecycle
 * (`StatusBadge`, only when no agent session backs the mark), agent activity
 * (`AgentStateMark` + the live region), and task phase (`PhaseBadge` on the
 * trigger for the glance, `PhaseMeter` in the popover for the read) — every
 * axis the overview grid card shows must also be legible once a workspace is
 * open, or opening it loses a signal the wall gave you.
 *
 * Popover sections (Separator-divided, the SECTION_LABEL grammar):
 *   - Task — the `PhaseMeter` (phase · step n/6 · note · when it was reported).
 *     First, because the trigger you just clicked is state-led: "where is this
 *     work" outranks "what ref is it on". Self-hides when no phase is reported.
 *   - Links — `TicketLinkage`, the issue(s) this workspace is FOR and the PR it
 *     produced, each independently clickable. Self-hides when there are none.
 *   - Identity — branch → base · agent/model · placement. Branch → base has NO
 *     other home (the work panel's Info tab carries agent/model/placement but
 *     not the branch pair), so this section is where the deleted strip's ref
 *     identity survives.
 *   - Changes — the StatTrio (ahead/behind/dirty) + the ±diff-line summary: a
 *     glanceable echo of the work panel's Diff tab, the strip's former counts.
 *   - Actions — the reversible verbs (pause/resume/respawn) fire DIRECTLY and,
 *     because a Popover button click doesn't dismiss the surface, the verb swaps
 *     IN PLACE as the status flips (pause → resume) without the popover closing.
 *   - Danger zone — the destructive `kill`, tinted `--status-error`, opening the
 *     `KillConfirmDialog` rendered as a SIBLING of the Popover so it survives the
 *     popover closing when the modal takes focus (the focus-handoff lesson).
 *
 * Deliberately NOT in the popover (both are zero-loss relocations, not drops):
 *   - Commits — the full `CommitList` lives in the work panel's Diff tab;
 *     duplicating it here would be dead weight. The Changes section's ±summary is
 *     the glance; the tab is the detail.
 *   - Sessions — session switch/track lives in the rail; the dead-pointer
 *     recovery picker lives on independently as the transcript's own
 *     empty-state picker (`AgentWorkspace`'s `emptyStatePicker`, page-wired),
 *     which only mounts when no session resolves — exactly the stuck case.
 *
 * Lifecycle wiring: this control owns the single `useWorkspaceActions` bundle;
 * which reversible verb shows follows the pure `availableActions(status,
 * placement)` gate (at most one at a time). The engine is the real precondition
 * gate, so an illegal action just surfaces its typed refusal.
 *
 * Test seams: `context-bar` (root, portaled into the header — unchanged so the
 * header-portal specs don't move), `identity-trigger` (trigger) opens
 * `branch-summary` (content); inside live the `stat-trio`/`placement-badge`
 * seams, `branch-delta-dot`, `phase-badge` (trigger) / `phase-meter` (popover),
 * `ticket-linkage`, `action-pause`/`action-resume`/`action-respawn`
 * (+ `action-error`), and `action-kill` (+ `kill-error`) which opens
 * `kill-confirm-dialog`. `session-state-live` is the sr-only state announcer.
 */
export function ContextBar({
  peek,
  live,
  phase = null,
  onKilled,
  className,
  viewSwitcher,
}: {
  peek: WorkspacePeekView;
  live: AgentLiveStatus;
  /** Task phase — it rides `WorkspaceActivityView`, not the peek, so the page
   *  resolves it off the SSE snapshot it already holds and hands it down. */
  phase?: PhaseView | null;
  /** After a successful kill the page navigates home (the peek would 404). */
  onKilled?: () => void;
  /** Optional extra styling for the root row. */
  className?: string;
  /** Page-wired pane view selector (Transcript/Terminal + lg split toggle). */
  viewSwitcher?: ReactNode;
}) {
  const s = peek.state;
  const ahead = peek.base_ahead;
  const behind = peek.base_behind;
  const dirty = peek.dirty_files;
  const changed = peek.diff_added > 0 || peek.diff_removed > 0;
  const hasDelta = ahead > 0 || behind > 0 || dirty > 0;
  const ticketRefs = s.ticket_refs ?? [];

  const { pause, resume, respawn, kill } = useWorkspaceActions(s.id);
  const [confirmKill, setConfirmKill] = useState(false);
  const actions = availableActions(s.status, s.placement);
  const canKill = actions.includes("kill");
  const busy = pause.isPending || resume.isPending || respawn.isPending || kill.isPending;

  // The reversible verbs that apply to this state — at most one shows at a time.
  // They fire directly (kill has its own confirm), closure-scoped so they read
  // the live mutation objects.
  const reversible = [
    { key: "pause", label: "Pause", Icon: Pause, run: () => pause.mutate(false) },
    { key: "resume", label: "Resume", Icon: Play, run: () => resume.mutate() },
    { key: "respawn", label: "Respawn", Icon: RotateCcw, run: () => respawn.mutate() },
  ].filter((d) => actions.includes(d.key as (typeof actions)[number]));

  const actionError = pause.error ?? resume.error ?? respawn.error;
  const actionErrorText =
    actionError instanceof GroveProtocolError
      ? actionError.message
      : actionError
        ? "Could not reach the daemon."
        : null;
  const killError =
    kill.error instanceof GroveProtocolError
      ? kill.error.message
      : kill.error
        ? "Could not reach the daemon."
        : null;

  const stateWord = live.hasSession ? agentStateLabel(live.state) : statusLabel(s.status);
  // blocked/error keep a short visible word — attention read in text, not just hue.
  const inlineLabel =
    live.hasSession && (live.state === "blocked" || live.state === "error")
      ? live.state === "blocked"
        ? "action required"
        : agentStateLabel(live.state)
      : null;

  return (
    <div
      data-testid="context-bar"
      className={cn("flex w-full min-w-0 items-center gap-2", className)}
    >
      {live.hasSession ? (
        <AgentStateMark state={live.state} className="shrink-0" />
      ) : (
        <StatusBadge status={s.status} size="sm" />
      )}

      {/* The state word, announced on CHANGE and nothing else: the raw
          task/prompt text must never reach a screen reader. */}
      <span data-testid="session-state-live" aria-live="polite" aria-atomic="true" className="sr-only">
        {stateWord}
      </span>

      {/* The page's document heading: a heading nested inside the trigger
          button is invalid ARIA (screen readers flatten it), so the h1 lives
          here sr-only and the button keeps its aria-label accessible name. */}
      <h1 className="sr-only">{s.title}</h1>

      <Popover>
        <PopoverTrigger asChild>
          <button
            type="button"
            data-testid="identity-trigger"
            aria-label={`${s.title} — ${stateWord}. Session details`}
            className={cn(
              "group flex min-w-0 flex-1 items-center gap-1 rounded-md px-1 py-0.5 text-left",
              "hover:bg-muted/50",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
            )}
          >
            <span
              className="min-w-0 flex-1 truncate text-sm font-semibold"
              title={s.description ?? s.title}
            >
              {s.title}
            </span>
            {/* The third axis at a glance — the same compact badge the grid
                card wears, so the wall and the open workspace agree. */}
            <PhaseBadge phase={phase} />
            {inlineLabel && (
              <span className="shrink-0 text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground">
                {inlineLabel}
              </span>
            )}
            {hasDelta && (
              <span
                data-testid="branch-delta-dot"
                aria-hidden
                className="size-1.5 shrink-0 rounded-full"
                style={{ backgroundColor: "var(--status-orphaned)" }}
              />
            )}
            <ChevronDown aria-hidden className="size-3.5 shrink-0 text-muted-foreground/60" />
          </button>
        </PopoverTrigger>
        <PopoverContent
          data-testid="branch-summary"
          align="start"
          className="max-h-[80vh] w-80 max-w-[calc(100vw-2rem)] overflow-y-auto p-0"
        >
          {/* One shared rhythm across every section: a SECTION_LABEL header + a
              `space-y-2 px-4 py-3` body, separated by a `Separator` hairline.
              (Tone-over-lines is the rule for PERSISTENT chrome; a floating
              Popover already draws its own edge, so a hairline INSIDE it is the
              deliberate overlay exception the sections read against.) */}

          {/* (a) Task — the phase axis in its READ register. First because the
              trigger is state-led: "where is this work" is the question that
              made you click. Self-hides when the agent reports no phase. */}
          {phase && (
            <>
              <div className="space-y-2 px-4 py-3">
                <span className={SECTION_LABEL}>Task</span>
                <PhaseMeter phase={phase} />
              </div>
              <Separator />
            </>
          )}

          {/* (b) Links — the issue(s) this workspace is FOR and the PR it
              produced, each independently clickable. Self-hides at zero refs. */}
          {ticketRefs.length > 0 && (
            <>
              <div className="space-y-2 px-4 py-3">
                <span className={SECTION_LABEL}>Links</span>
                <TicketLinkage refs={ticketRefs} className="flex-wrap" />
              </div>
              <Separator />
            </>
          )}

          {/* (c) Identity — the ref pills + placement. Branch → base has no
              other home. */}
          <div className="space-y-2 px-4 py-3">
            <span className={SECTION_LABEL}>Identity</span>
            <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1">
              <Badge
                variant="outline"
                className="max-w-[12rem] truncate border-[var(--ref-branch)]/40 font-mono text-[var(--ref-branch)]"
                title={s.branch}
              >
                {s.branch}
              </Badge>
              <span aria-hidden className="text-muted-foreground/50">
                ←
              </span>
              <Badge variant="outline" className="font-mono" title={s.base_branch}>
                {s.base_branch}
              </Badge>
              <span aria-hidden className="text-muted-foreground/40">
                ·
              </span>
              <Badge
                variant="outline"
                className="border-[var(--ref-info)]/40 font-mono text-[var(--ref-info)]"
                title={s.agent_name}
              >
                {s.agent_name}
                {live.model ? `/${live.model}` : ""}
              </Badge>
              <PlacementBadge placement={s.placement} size="sm" />
              {/* Runtime is labeled here, not a bare mark: this popover is the
                  read-deeply surface, and the isolation boundary is worth a
                  word once there is room for one. Always present — see
                  RuntimeBadge for why this axis is never silent. */}
              <RuntimeBadge
                runtime={s.runtime}
                runtimeFallbackReason={s.runtime_fallback_reason}
                runtimeDefaultConfig={s.runtime_default_config}
                size="sm"
              />
            </div>
          </div>

          <Separator />

          {/* (d) Changes — the strip's former counts, a glance echo of the Diff
              tab (the full CommitList stays in that tab, never duplicated here). */}
          <div className="space-y-2 px-4 py-3">
            <span className={SECTION_LABEL}>Changes</span>
            <StatTrio ahead={ahead} behind={behind} dirty={dirty} />
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
          </div>

          {/* (e) Actions — the reversible lifecycle verbs. At most one shows per
              state; they fire directly and the open popover re-renders the
              swapped verb as the status flips. */}
          {reversible.length > 0 && (
            <>
              <Separator />
              <div className="space-y-2 px-4 py-3">
                <span className={SECTION_LABEL}>Actions</span>
                <div className="flex flex-col gap-0.5">
                  {reversible.map((d) => (
                    <button
                      key={d.key}
                      type="button"
                      data-testid={`action-${d.key}`}
                      disabled={busy}
                      onClick={d.run}
                      className={cn(ACTION_ROW, "hover:bg-muted")}
                    >
                      <d.Icon className="size-4" aria-hidden />
                      {d.label}
                    </button>
                  ))}
                </div>
                {actionErrorText && (
                  <p role="alert" data-testid="action-error" className="text-xs text-[var(--status-error)]">
                    {actionErrorText}
                  </p>
                )}
              </div>
            </>
          )}

          {/* (f) Danger zone — the destructive kill, sitting beside the
              branch/worktree identity it tears down. Self-hides when kill isn't
              a legal action for the current state. */}
          {canKill && (
            <>
              <Separator />
              <div className="space-y-2 px-4 py-3">
                <span className={SECTION_LABEL}>Danger zone</span>
                <button
                  type="button"
                  data-testid="action-kill"
                  disabled={busy}
                  onClick={() => setConfirmKill(true)}
                  className={cn(
                    ACTION_ROW,
                    "text-[var(--status-error)] hover:bg-[var(--status-error)]/10",
                  )}
                >
                  {kill.isPending ? (
                    <LoaderCircle className="size-4 animate-spin" aria-hidden />
                  ) : (
                    <Trash2 className="size-4" aria-hidden />
                  )}
                  Kill workspace
                </button>
                {killError && (
                  <p role="alert" data-testid="kill-error" className="text-xs text-[var(--status-error)]">
                    {killError}
                  </p>
                )}
              </div>
            </>
          )}
        </PopoverContent>
      </Popover>

      {/* The view switcher floats in a quiet tone well — a grouped control
          without a bordered pill (tone over lines). `flex-1` on the trigger
          pushes it hard-right. */}
      <div className="flex shrink-0 items-center gap-1">
        <div className="flex items-center rounded-md bg-muted/40 px-0.5 py-0.5">{viewSwitcher}</div>
      </div>

      {/* Sibling of the Popover so it stays mounted if the popover closes when
          the modal takes focus — the focus-handoff lesson. */}
      <KillConfirmDialog
        open={confirmKill}
        onOpenChange={setConfirmKill}
        state={s}
        pending={kill.isPending}
        onConfirm={(deleteBranch) =>
          kill.mutate(deleteBranch, {
            onSuccess: () => {
              setConfirmKill(false);
              onKilled?.();
            },
          })
        }
      />
    </div>
  );
}
