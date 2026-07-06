"use client";

import { CheckIcon, ChevronDownIcon, History, Link2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { AgentStateMark } from "@/components/shared/state-mark";
import { MetaRow } from "@/components/shared/meta";
import { RelativeTime } from "@/components/shared/relative-time";
import { agentStateLabel } from "@/lib/grove/agent-state-tokens";
import { cn } from "@/lib/utils";
import type { SessionSummaryView } from "@/lib/grove/types";

/** Same truncation convention as commit SHAs (`commit-list.tsx`) — a session
 *  id is a UUID/opaque token, never meaningful in full at a glance. */
function shortId(id: string): string {
  return id.slice(0, 7);
}

/**
 * The workspace's session picker + remap affordance (issues #121, #132). Quiet,
 * secondary chrome — a ghost trigger, not a hero element — riding the header
 * identity cluster since the chrome-teardown (#130), so the trigger is
 * borderless and the remap-error notice lives INSIDE the popover (a
 * header-anchored floating notice would have nowhere to sit).
 *
 * TWO modes, one component:
 *
 * - **Switch** (`sessions.length > 1`): the multi-session case. `onSelect` is a
 *   pure client-side view change (which recorded session the transcript renders);
 *   "Make primary" (`onMakePrimary`) is the separate durable remap — a POST that
 *   repins the daemon's `agent_session_id` (see `useRemapSession`). The two are
 *   decoupled: looking at a session doesn't repin it.
 *
 * - **Track** (`candidates` non-empty, ≤1 tracked session): the stuck case (#132)
 *   — the workspace tracks a DEAD pointer whose live successor the adoption gate
 *   rejects, so the gated list self-hides the switcher and there's no way out.
 *   `candidates` is the UNGATED cwd-scoped set (`?candidates=true`) that KEEPS the
 *   dropped successor; each row's one action is `onMakePrimary` (adopt = remap),
 *   the human supplying the attribution the gate withholds. The page fetches
 *   candidates lazily and passes them ONLY while stuck, so this mode never shows
 *   for a healthy single-session workspace.
 *
 * Rows never nest an interactive element inside another (a Radix-menu-item
 * pitfall): a switch row's select control and its "Make primary" button are
 * SIBLINGS; a candidate row has a single pick button with the meta as a
 * non-interactive sibling div.
 *
 * The list BODY is factored out as `SessionPickerList` (the label + rows + remap
 * notice, no Popover shell) so it can drop straight into the header identity
 * popover's Sessions section (`session-identity.tsx`). `SessionPicker` stays a
 * thin Popover+trigger wrapper over that list — still the ChatPanel empty-state
 * / stuck-recovery affordance.
 *
 * Test seam: `session-picker`, `session-picker-trigger` (wrapper only),
 * `session-picker-item` (+ `data-session-id`, `data-selected`),
 * `session-picker-select`, `session-picker-make-primary`,
 * `session-picker-candidate` (+ `data-session-id`), `session-picker-track`,
 * `session-picker-notice`.
 */
type SessionPickerProps = {
  sessions: SessionSummaryView[];
  /**
   * Ungated candidate sessions to adopt when none is usefully tracked (#132).
   * Absent/empty ⇒ no track affordance; the picker falls back to switch-or-null.
   */
  candidates?: SessionSummaryView[];
  selectedId: string | null;
  onSelect: (sessionId: string) => void;
  onMakePrimary: (sessionId: string) => void;
  /** The session id currently mid-remap (disables its own button), or null. */
  pendingId: string | null;
  /** The last remap attempt's refusal message, or null once dismissed/retried. */
  error: string | null;
};

/** Resolve whether the picker has anything to show, and which session leads. */
function resolvePicker(sessions: SessionSummaryView[], candidates: SessionSummaryView[], selectedId: string | null) {
  const canSwitch = sessions.length > 1;
  // Track mode is the escape hatch offered ONLY when there's nothing to switch
  // between — so a healthy multi-session workspace keeps the plain switcher.
  const canTrack = !canSwitch && candidates.length > 0;
  const selected = canSwitch
    ? (sessions.find((s) => s.session_id === selectedId) ?? sessions[0])
    : null;
  return { canSwitch, canTrack, selected };
}

/**
 * The picker's list BODY — label, switch/track rows, and the remap notice — with
 * NO Popover shell, so it composes both inside `SessionPicker`'s popover AND
 * inside the header identity popover's Sessions section. Returns null when there
 * is neither a switch nor a track to offer (callers gate too, but this keeps the
 * body honest on its own).
 */
export function SessionPickerList({
  sessions,
  candidates,
  selectedId,
  onSelect,
  onMakePrimary,
  pendingId,
  error,
}: SessionPickerProps) {
  const trackList = candidates ?? [];
  const { canSwitch, canTrack, selected } = resolvePicker(sessions, trackList, selectedId);
  if (!canSwitch && !canTrack) return null;

  return (
    <div className="flex flex-col">
      {canSwitch && selected ? (
        <>
          <p className="px-2 py-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
            Sessions
          </p>
          <ul className="flex flex-col gap-0.5">
            {sessions.map((s) => (
              <SessionRow
                key={s.session_id}
                session={s}
                isSelected={s.session_id === selected.session_id}
                pending={pendingId === s.session_id}
                onSelect={() => onSelect(s.session_id)}
                onMakePrimary={() => onMakePrimary(s.session_id)}
              />
            ))}
          </ul>
        </>
      ) : (
        <>
          <p className="px-2 py-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
            Untracked sessions
          </p>
          {/* Progressive disclosure: the WHY of an empty transcript lives one
              hop in (the popover), not on the resting header chrome. Calm,
              not alarming — a plain muted line, no error styling. */}
          <p className="px-2 pb-1.5 text-xs text-muted-foreground">
            Grove isn&apos;t following a session here. Pick the one it should track.
          </p>
          <ul className="flex flex-col gap-0.5">
            {trackList.map((s) => (
              <CandidateRow
                key={s.session_id}
                session={s}
                pending={pendingId === s.session_id}
                onTrack={() => onMakePrimary(s.session_id)}
              />
            ))}
          </ul>
        </>
      )}
      {/* The remap refusal notice lives at the list's foot now (#130): a
          header-anchored trigger has nowhere to float a sibling notice.
          Borderless-quiet — a passing note, not a boxed alert. */}
      {error && (
        <p
          data-testid="session-picker-notice"
          role="status"
          className="mt-1 rounded-md bg-muted/40 px-2 py-1.5 text-xs text-muted-foreground"
        >
          {error}
        </p>
      )}
    </div>
  );
}

export function SessionPicker(props: SessionPickerProps) {
  const { sessions, candidates, selectedId } = props;
  const trackList = candidates ?? [];
  const { canSwitch, canTrack, selected } = resolvePicker(sessions, trackList, selectedId);
  if (!canSwitch && !canTrack) return null;

  return (
    <div data-testid="session-picker" className="shrink-0">
      <Popover>
        <PopoverTrigger asChild>
          {selected ? (
            <Button
              variant="ghost"
              size="xs"
              data-testid="session-picker-trigger"
              aria-label={`Switch session — viewing ${shortId(selected.session_id)}`}
              className="gap-1.5 font-normal text-muted-foreground"
            >
              <History aria-hidden />
              <span className="max-w-32 truncate font-mono">{shortId(selected.session_id)}</span>
              <ChevronDownIcon className="opacity-60" aria-hidden />
            </Button>
          ) : (
            <Button
              variant="ghost"
              size="xs"
              data-testid="session-picker-trigger"
              aria-label="Track a session"
              className="gap-1.5 font-normal text-muted-foreground"
            >
              <Link2 aria-hidden />
              <span>Track a session</span>
              <ChevronDownIcon className="opacity-60" aria-hidden />
            </Button>
          )}
        </PopoverTrigger>
        <PopoverContent align="end" className="w-80 max-h-96 overflow-y-auto p-1">
          <SessionPickerList {...props} />
        </PopoverContent>
      </Popover>
    </div>
  );
}

function SessionRow({
  session,
  isSelected,
  pending,
  onSelect,
  onMakePrimary,
}: {
  session: SessionSummaryView;
  isSelected: boolean;
  pending: boolean;
  onSelect: () => void;
  onMakePrimary: () => void;
}) {
  const label = session.title || session.first_prompt || "untitled session";
  return (
    <li
      data-testid="session-picker-item"
      data-session-id={session.session_id}
      data-selected={isSelected}
      className={cn("rounded-md px-2 py-1.5", isSelected ? "bg-accent" : "hover:bg-accent/50")}
    >
      <button
        type="button"
        data-testid="session-picker-select"
        onClick={onSelect}
        className={cn(
          "flex w-full items-center gap-1.5 text-left",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
        )}
      >
        <AgentStateMark state={session.activity.state} />
        <span className="min-w-0 flex-1 truncate text-xs font-medium">{label}</span>
        {isSelected && (
          <CheckIcon aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
        )}
      </button>
      <div className="mt-0.5 flex items-center gap-1.5 pl-5">
        <MetaRow className="text-[11px]">
          <span className="font-mono" title={session.session_id}>
            {shortId(session.session_id)}
          </span>
          <RelativeTime iso={session.modified_at} />
          <span>{agentStateLabel(session.activity.state)}</span>
        </MetaRow>
        {!isSelected && (
          <button
            type="button"
            data-testid="session-picker-make-primary"
            disabled={pending}
            onClick={onMakePrimary}
            className={cn(
              "ml-auto shrink-0 text-[11px] font-medium text-muted-foreground",
              "hover:text-foreground hover:underline underline-offset-2",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
              "disabled:pointer-events-none disabled:opacity-50",
            )}
          >
            {pending ? "Pinning…" : "Make primary"}
          </button>
        )}
      </div>
    </li>
  );
}

/**
 * A candidate row (track mode, #132): one pick button whose only action is to
 * ADOPT this session as the workspace's primary (a remap). There is no "selected"
 * or "switch view" here — nothing is tracked yet, so picking IS the whole gesture.
 * The pick button carries only phrasing content; the meta div is a NON-interactive
 * sibling (a `<div>`/`MetaRow` can't live inside a `<button>`), so the row keeps
 * exactly one interactive element (the Radix sibling rule).
 */
function CandidateRow({
  session,
  pending,
  onTrack,
}: {
  session: SessionSummaryView;
  pending: boolean;
  onTrack: () => void;
}) {
  const label = session.title || session.first_prompt || "untitled session";
  return (
    <li
      data-testid="session-picker-candidate"
      data-session-id={session.session_id}
      className="rounded-md px-2 py-1.5 hover:bg-accent/50"
    >
      <button
        type="button"
        data-testid="session-picker-track"
        disabled={pending}
        onClick={onTrack}
        className={cn(
          "flex w-full items-center gap-1.5 text-left",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
          "disabled:pointer-events-none disabled:opacity-50",
        )}
      >
        <AgentStateMark state={session.activity.state} />
        <span className="min-w-0 flex-1 truncate text-xs font-medium">{label}</span>
        <span className="shrink-0 text-[11px] font-medium text-muted-foreground">
          {pending ? "Tracking…" : "Track"}
        </span>
      </button>
      <div className="mt-0.5 pl-5">
        <MetaRow className="text-[11px]">
          <span className="font-mono" title={session.session_id}>
            {shortId(session.session_id)}
          </span>
          <RelativeTime iso={session.modified_at} />
          <span>{agentStateLabel(session.activity.state)}</span>
        </MetaRow>
      </div>
    </li>
  );
}
