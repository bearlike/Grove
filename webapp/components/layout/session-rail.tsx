"use client";

import { useEffect, useMemo, useRef } from "react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { MoreHorizontal, Pin, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { LandingRing } from "@/components/shared/landing-ring";
import { MetaRow } from "@/components/shared/meta";
import { RuntimeMark } from "@/components/shared/runtime-mark";
import { PhaseBadge } from "@/components/workspace/phase-badge";
import { relativeTimeLabel } from "@/components/shared/relative-time";
import { agentStateLabel, ATTENTION_STATES } from "@/lib/grove/agent-state-tokens";
import { useProjectSessionsAll, useRemapSession } from "@/lib/grove/hooks";
import type { DashboardFacets } from "@/lib/grove/dashboard-filter";
import type {
  AgentActivityState,
  AgentActivityView,
  DashboardSnapshotView,
  PhaseView,
  Runtime,
  SessionSummaryView,
  WorkspaceActivityView,
} from "@/lib/grove/types";
import { cn } from "@/lib/utils";

/**
 * The session rail — the rail's scrollable BODY, Grove's fleet as a
 * persistent left "thread list". A FLAT, cross-project list ordered by one
 * rule: `modified_at` DESC (latest first). No project sections, no date
 * buckets, no attention pin — project identity rides every row's meta line
 * and attention signals inline (dot color + a faint waiting tint), so the
 * filter menu (`SidebarFilter`) is the single organizing instrument.
 *
 * Row shape (the Codex-style session-list pattern): TWO quiet lines on a
 * fixed rhythm (`leading-5` head / `leading-4` meta, `gap-0.5`) so every row
 * is structurally identical —
 *   Line 1: a 6px `StateDot` (the session-rail-dot atom, in the state's
 *     `--agent-*` hue) · the title (truncate, first-class) · the created-ago,
 *     right-aligned + muted (the card's "state · title · time" rhythm). On hover
 *     the row's `group-hover:pe-9` slides it clear of the ⋯ menu.
 *   Line 2 (meta, `text-[11px] text-muted-foreground`, middot-separated):
 *     project name (a subtle DOTTED underline is the quiet cue separating project
 *     from branch — both are muted, so tone can't) · branch (font-mono, plain
 *     muted — NOT the teal ref token, the rail stays quiet) · `+N/−M` change
 *     stats (`+N` in `--ref-add`, `−M` in `--ref-remove`, tabular) · the
 *     `PhaseBadge` glance (the task-phase glyph + `n/6`, from the row's own
 *     live workspace; absent when the agent reports no phase). Change stats
 *     come from the LIVE workspace (`WorkspaceActivityView.diff_added`/
 *     `.diff_removed`) — real ±line counts, never faked from `dirty_files`/ahead/
 *     behind. A row whose workspace carries zero change data shows nothing in that
 *     slot (blank beats noise). Truncation priority: title first, then branch,
 *     project never fully vanishes (`shrink-0 max-w-[45%]`).
 *
 * Default visibility = actionable only. A row is "mapped" when its
 * `workspace_id !== null` AND that workspace is present in the live snapshot
 * (`wsById.has(...)`). Unmapped / metadata-only rows are HIDDEN by default
 * (`showUnmapped`, persisted, default false); when hidden and at least one
 * exists, ONE quiet "N unmapped session(s) hidden" note at the list end flips
 * `showUnmapped` on — an honesty affordance, not chrome.
 *
 * Data path (settled, wire-data.md Decision 1): the ungated project-wide
 * `GET /sessions?repo=` per project via `useProjectSessionsAll` (15 s history
 * poll). On top of the poll the rail layers an SSE "refresh now" invalidation:
 * a cheap fingerprint of the live activity snapshot fires an `["project-sessions"]`
 * invalidation the moment a run's state actually changes.
 *
 * Live state overlay: a row's DISPLAYED state + attention come from the live
 * snapshot (by session id) when the daemon is tracking it, else from the session
 * summary's own recorded activity — so the rail and the cards never disagree.
 *
 * Metadata-only rows (`workspace_id === null`): there is no daemon route to open
 * their transcript, so they render dimmed, "history only", NOT navigable — a hard,
 * wire-visible rule, never a heuristic. Copy never promises "every session ever".
 *
 * Collapse is a shell concern: the whole rail hides (`w-0`), so there is no
 * collapsed icon-strip variant.
 *
 * Test seams: `session-rail`, `session-rail-new`, `session-rail-row` (+
 * `data-session-id`, `data-workspace-id`, `data-navigable`, `data-mapped`,
 * `data-attention`, `data-active`), `session-rail-dot`, `session-rail-project-name`,
 * `session-rail-age`, `session-rail-changes`, `phase-badge`, `session-rail-row-menu`,
 * `session-rail-make-primary`, `session-rail-hidden-note`, `session-rail-empty`,
 * `session-rail-empty-clear`.
 */
export function SessionRail({
  snapshot,
  facets,
  query,
  hiddenStates = [],
  attentionOnly = false,
  hiddenProjects = [],
  showUnmapped = false,
  onShowUnmapped,
  onClearFilters,
  onNavigate,
}: {
  snapshot: DashboardSnapshotView | null;
  facets: DashboardFacets | null;
  /** Free-text filter over title / prompt / branch (the rail search box). */
  query: string;
  /** Agent states to HIDE (the compact filter); applied to the LIVE-overlaid state. */
  hiddenStates?: AgentActivityState[];
  /** When true, keep only rows the agent-state flags for a human. */
  attentionOnly?: boolean;
  /** `repo_root`s to HIDE entirely (the filter menu's Projects section). */
  hiddenProjects?: string[];
  /** Reveal the unmapped/metadata-only rows (default false — actionable only). */
  showUnmapped?: boolean;
  /** Flip `showUnmapped` on — the hidden-note button. */
  onShowUnmapped?: () => void;
  /** Clear EVERY filter (query, states, projects, attention, showUnmapped). */
  onClearFilters?: () => void;
  /** Mobile (Sheet) only — dismiss the drawer after a row navigates. */
  onNavigate?: () => void;
}) {
  const repos = useMemo(() => (facets?.projects ?? []).map((p) => p.repo_root), [facets]);
  const { byRepo, isLoading } = useProjectSessionsAll(repos);

  // Live overlay + workspace lookup, both derived once from the snapshot. The
  // workspace map holds the full `WorkspaceActivityView` (not just its state) so
  // a row can read the live ±diff-line counts for its meta slot.
  const { liveActivity, wsById } = useMemo(() => buildSnapshotIndex(snapshot), [snapshot]);

  // SSE "refresh now": invalidate the project-session lists whenever the live
  // snapshot's per-workspace state fingerprint changes — skip the first run so
  // mount doesn't double-fetch. The 15 s poll is the backstop.
  const queryClient = useQueryClient();
  const fingerprint = useMemo(
    () =>
      (snapshot?.projects ?? [])
        .flatMap((p) =>
          p.workspaces.map(
            (w) =>
              `${w.state.id}:${w.state.status}:${w.sessions[0]?.activity.state ?? ""}:${w.needs_attention}`,
          ),
        )
        .join("|"),
    [snapshot],
  );
  const firstRun = useRef(true);
  useEffect(() => {
    if (firstRun.current) {
      firstRun.current = false;
      return;
    }
    void queryClient.invalidateQueries({ queryKey: ["project-sessions"] });
  }, [fingerprint, queryClient]);

  // Which row the current route + `?s=` selection points at (the active row).
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const activeWorkspaceId = pathname?.startsWith("/w/") ? pathname.slice(3) : null;
  const activeSessionId = searchParams.get("s");

  const q = query.trim().toLowerCase();
  const hidden = useMemo(() => new Set(hiddenStates), [hiddenStates]);
  const hiddenProj = useMemo(() => new Set(hiddenProjects), [hiddenProjects]);

  // The flat, cross-project entry list. Each project's rows carry that project's
  // name (provenance rides every row now that the sections are gone); filters
  // apply here, then the whole list sorts by `modified_at` DESC — the one rule.
  const entries = useMemo(() => {
    const out: RailEntry[] = [];
    for (const p of facets?.projects ?? []) {
      if (hiddenProj.has(p.repo_root)) continue;
      for (const s of byRepo.get(p.repo_root) ?? []) {
        if (!matchesQuery(s, q)) continue;
        // A row's `activity` is nullable on the wire — null means "not parsed
        // at this scope", so it degrades to `unknown` rather than a
        // fabricated idle. The rail reads the project scope, which always
        // parses, so this is a type floor.
        const activity = liveActivity.get(s.session_id) ?? s.activity;
        if (hidden.has(activity?.state ?? "unknown")) continue;
        if (attentionOnly && !isAttention(activity)) continue;
        out.push({ session: s, activity, repoName: p.repo_name });
      }
    }
    out.sort(byModifiedDesc);
    return out;
  }, [facets, byRepo, liveActivity, q, hidden, hiddenProj, attentionOnly]);

  const isMapped = (s: SessionSummaryView): boolean =>
    s.workspace_id !== null && wsById.has(s.workspace_id);
  const mapped = entries.filter((e) => isMapped(e.session));
  const unmappedCount = entries.length - mapped.length;
  const visible = showUnmapped ? entries : mapped;

  const isActive = (s: SessionSummaryView): boolean =>
    s.workspace_id !== null &&
    s.workspace_id === activeWorkspaceId &&
    (activeSessionId === null || activeSessionId === s.session_id);

  const rowProps = (r: RailEntry) => {
    const ws = r.session.workspace_id ? (wsById.get(r.session.workspace_id) ?? null) : null;
    return {
      row: r.session,
      activity: r.activity,
      repoName: r.repoName,
      mapped: isMapped(r.session),
      added: ws?.diff_added ?? 0,
      removed: ws?.diff_removed ?? 0,
      phase: ws?.phase ?? null,
      runtime: ws?.state.runtime ?? null,
      wsStatus: ws?.state.status ?? null,
      pausedAt: ws?.state.paused_at ?? null,
      active: isActive(r.session),
      onNavigate,
    };
  };

  // "Genuinely empty" (no sessions anywhere) vs "filters hid them all": the former
  // is computed off the raw lists, before any filter, so an active filter never
  // reads as an empty account.
  const anySessionExists = (facets?.projects ?? []).some(
    (p) => (byRepo.get(p.repo_root)?.length ?? 0) > 0,
  );
  const explicitFilterActive =
    q !== "" || hidden.size > 0 || attentionOnly || hiddenProj.size > 0;

  if (isLoading && entries.length === 0 && !anySessionExists) {
    return (
      <div data-testid="session-rail" className="flex flex-col gap-1 px-2 py-2">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-10 w-full" />
        ))}
      </div>
    );
  }

  return (
    <div data-testid="session-rail" className="flex flex-col gap-0.5 px-2 py-2">
      <NewSessionRow onNavigate={onNavigate} />

      {visible.length > 0 && (
        <ul className="flex flex-col gap-0.5 pt-1">
          {visible.map((r) => (
            <RailRow key={r.session.session_id} attention={isAttention(r.activity)} {...rowProps(r)} />
          ))}
        </ul>
      )}

      {!showUnmapped && unmappedCount > 0 && (
        <button
          type="button"
          data-testid="session-rail-hidden-note"
          onClick={onShowUnmapped}
          className="mt-1 rounded-md px-2.5 py-1.5 text-left text-[11px] text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
        >
          {unmappedCount} unmapped session{unmappedCount > 1 ? "s" : ""} hidden
        </button>
      )}

      {visible.length === 0 && explicitFilterActive && (
        <div
          data-testid="session-rail-empty"
          className="flex flex-col items-center gap-2 px-2 py-6 text-center text-xs text-muted-foreground"
        >
          <p>No sessions match the active filters.</p>
          <button
            type="button"
            data-testid="session-rail-empty-clear"
            onClick={onClearFilters}
            className="rounded-md px-2 py-1 font-medium text-foreground transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            Clear filters
          </button>
        </div>
      )}

      {visible.length === 0 && !explicitFilterActive && !anySessionExists && (
        <p
          data-testid="session-rail-empty"
          className="px-2 py-6 text-center text-xs text-muted-foreground"
        >
          {q ? "No sessions match your search." : "No sessions yet — start one with the composer."}
        </p>
      )}
    </div>
  );
}

/**
 * The quiet "New session" ghost row at the rail top — the modern-chat
 * new-thread affordance. Links to `/` (the hero composer IS the create surface).
 */
function NewSessionRow({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <Button
      asChild
      variant="ghost"
      className="h-8 w-full justify-start gap-2 rounded-md px-2.5 text-sm font-normal hover:bg-muted"
    >
      <Link href="/" onClick={onNavigate} data-testid="session-rail-new">
        <Plus className="shrink-0" aria-hidden />
        <span>New session</span>
      </Link>
    </Button>
  );
}

// ─── Row ─────────────────────────────────────────────────────────────────────

type RailEntry = {
  session: SessionSummaryView;
  /** Null = the listing never parsed a transcript; renders `unknown`. */
  activity: AgentActivityView | null;
  /** The owning project's display name — provenance rides every row. */
  repoName: string;
};

/**
 * The quiet state cue: a 6px dot in the state's `--agent-*` hue before the
 * title. Color is an enhancement, never the only signal — the row carries the
 * state label in its `aria-label`/`title`, so the dot is never read alone.
 */
function StateDot({ state }: { state: AgentActivityState }) {
  return (
    <span
      aria-hidden
      data-testid="session-rail-dot"
      data-state={state}
      className="size-1.5 shrink-0 rounded-full"
      style={{ backgroundColor: `var(--agent-${state}, var(--agent-unknown))` }}
    />
  );
}

/**
 * The `+N/−M` change slot on the meta line — real ±line counts from the live
 * workspace's diff (`WorkspaceActivityView.diff_added`/`.diff_removed`).
 *
 * Callers MUST gate the mount (`hasChanges && <ChangeStat/>`): `MetaRow` drops
 * falsy CHILDREN, but a `<ChangeStat/>` element is truthy even when it renders
 * null (`Children.toArray` never invokes the component) — an unguarded slot
 * leaves a dangling trailing middot on every clean row. The internal guard
 * stays as defense in depth only.
 */
function ChangeStat({ added, removed }: { added: number; removed: number }) {
  if (added <= 0 && removed <= 0) return null;
  return (
    <span
      data-testid="session-rail-changes"
      className="inline-flex shrink-0 items-center gap-1 tabular-nums"
    >
      {added > 0 && <span style={{ color: "var(--ref-add)" }}>+{added}</span>}
      {removed > 0 && <span style={{ color: "var(--ref-remove)" }}>−{removed}</span>}
    </span>
  );
}

function RailRow({
  row,
  activity,
  repoName,
  mapped,
  added,
  removed,
  phase,
  runtime,
  wsStatus,
  pausedAt,
  attention = false,
  active,
  onNavigate,
}: {
  row: SessionSummaryView;
  activity: AgentActivityView | null;
  repoName: string;
  mapped: boolean;
  added: number;
  removed: number;
  /** The owning workspace's task phase, when it reports one. */
  phase: PhaseView | null;
  /** The owning workspace's runtime. `null` ONLY for a history-only row with no
   *  live workspace behind it, where the isolation boundary is genuinely
   *  unknowable — never a defaulted "host". Every mapped row carries a mark. */
  runtime: Runtime | null;
  wsStatus: string | null;
  pausedAt: string | null;
  attention?: boolean;
  active: boolean;
  onNavigate?: () => void;
}) {
  const navigable = row.workspace_id !== null;
  const label = row.title || row.first_prompt || "untitled session";
  const branch = row.git_branch || row.workspace_branch || null;
  const paused = wsStatus === "paused";
  const state = activity?.state ?? "unknown";
  const stateLabel = agentStateLabel(state);
  const ageLabel = row.created_at ? relativeTimeLabel(row.created_at) : null;

  // Line 1 (dot · title · created-ago). The age is right-aligned and muted — the
  // same "state · title · time" rhythm the card header wears. It's the
  // static `relativeTimeLabel` (not a per-row live-ticking <RelativeTime>): the
  // 15 s poll + SSE invalidation re-render the rail often enough to keep it fresh
  // without N intervals. On hover the row's `group-hover:pe-9` slides it left so
  // the ⋯ menu (top-right) never lands on top of it. `leading-5` matches the
  // title's box so both sit on one baseline; a fixed line height per row.
  const head = (
    <span className="flex items-center gap-2">
      <StateDot state={state} />
      <span className="min-w-0 flex-1 truncate text-sm leading-5">{label}</span>
      {ageLabel ? (
        <span
          data-testid="session-rail-age"
          title={row.created_at ?? undefined}
          className="shrink-0 text-[11px] leading-5 tabular-nums text-muted-foreground"
        >
          {ageLabel}
        </span>
      ) : null}
    </span>
  );

  // Line 2 — the provenance meta (project · branch · ±changes), one truncating
  // row (`flex-nowrap overflow-hidden`) indented under the title (`ps-3.5`).
  //   · project carries a subtle DOTTED underline — the quiet cue that separates
  //     project identity from the branch (both are muted, so tone can't do it).
  //     `shrink-0 max-w-[45%]` keeps it visible always (truncates only past 45%).
  //   · branch is plain muted mono — NOT the teal ref token, so the rail stays
  //     quiet; `min-w-0` (default shrink) makes IT give way first when tight.
  //   · ± carry the only hue (`--ref-add`/`--ref-remove`), `shrink-0`, always full.
  // Truncation priority falls out of the flex: title (line 1) is first-class,
  // then branch shrinks, and project never fully vanishes.
  const meta = (
    <MetaRow className="ps-3.5 flex-nowrap overflow-hidden text-[11px] leading-4">
      {/* The isolation axis leads the meta line, exactly as it leads line 2 of
          the TUI card, and `shrink-0` so the row's truncation can never eat the
          one mark that says whether this agent sits inside a boundary. */}
      {runtime && <RuntimeMark runtime={runtime} className="shrink-0" />}
      <span
        data-testid="session-rail-project-name"
        className="max-w-[45%] shrink-0 truncate underline decoration-muted-foreground/40 decoration-dotted underline-offset-2"
      >
        {repoName}
      </span>
      {branch ? (
        <span title={branch} className="min-w-0 truncate font-mono">
          {branch}
        </span>
      ) : null}
      {/* Gated at JSX level so MetaRow sees a falsy child and drops the middot
          for clean rows — see the ChangeStat docstring for why null-inside
          isn't enough. `PhaseBadge` needs the SAME gate for the same reason
          (it renders null on a null phase, but the ELEMENT is still truthy). */}
      {(added > 0 || removed > 0) && <ChangeStat added={added} removed={removed} />}
      {/* The third axis, in its glance register: a text sigil, not a lucide
          icon — the rail's no-icons rule is about chrome, and this reads as
          the same kind of mark as `+N/−M`. */}
      {phase && <PhaseBadge phase={phase} />}
    </MetaRow>
  );

  if (!navigable) {
    // Metadata-only: no owning workspace id → no transcript route. Dimmed and
    // inert; the "history only" note lives in the tooltip, not a visible line.
    return (
      <li
        data-testid="session-rail-row"
        data-session-id={row.session_id}
        data-workspace-id=""
        data-navigable="false"
        data-mapped={mapped}
        data-attention={attention}
        data-active="false"
        title="History only — this session has no live workspace to open."
        className="flex flex-col gap-0.5 rounded-md px-2.5 py-1.5 opacity-55"
      >
        {head}
        {meta}
      </li>
    );
  }

  const when =
    paused && pausedAt
      ? `paused ${relativeTimeLabel(pausedAt)}`
      : row.modified_at
        ? relativeTimeLabel(row.modified_at)
        : null;
  const tip = [`${label} — ${stateLabel}`, activity?.model || null, when]
    .filter(Boolean)
    .join(" · ");

  return (
    <li
      data-testid="session-rail-row"
      data-session-id={row.session_id}
      data-workspace-id={row.workspace_id ?? ""}
      data-navigable="true"
      data-mapped={mapped}
      data-attention={attention}
      data-active={active}
      // Attention rows signal inline now (no more top pin): a faint waiting tint
      // on the row itself. It's a base-layer class so the `hover:`/`data-active`
      // muted fills (higher-specificity variants) still win when they apply.
      className={cn(
        "group relative flex rounded-md transition-colors hover:bg-muted data-[active=true]:bg-muted",
        attention && "bg-[color-mix(in_oklab,var(--agent-waiting)_8%,transparent)]",
      )}
    >
      <Link
        href={`/w/${row.workspace_id}?s=${encodeURIComponent(row.session_id)}`}
        onClick={onNavigate}
        title={tip}
        aria-label={`${label} — ${stateLabel}`}
        className="flex min-w-0 flex-1 flex-col gap-0.5 rounded-md px-2.5 py-1.5 group-hover:pe-9 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
      >
        {head}
        {meta}
      </Link>
      <RailRowMenu workspaceId={row.workspace_id!} sessionId={row.session_id} />
      {/* One-shot terracotta ring when a just-created session's row first appears
          in the rail — the default-flow twin of the card's ring. */}
      <LandingRing landedAt={row.created_at} className="rounded-md" />
    </li>
  );
}

/**
 * The row-level lifecycle overflow — the "make primary" session-track that
 * repins the daemon's tracked session. A sibling of the row Link (never nested —
 * the interactive-in-interactive pitfall), revealed on hover/focus; the link
 * reserves space with `group-hover:pe-9` so the title never slides under it.
 */
function RailRowMenu({ workspaceId, sessionId }: { workspaceId: string; sessionId: string }) {
  const remap = useRemapSession(workspaceId);
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          data-testid="session-rail-row-menu"
          aria-label="Session actions"
          // Hover-reveal on the desktop rail (lg+); always visible below lg, where
          // the rail lives in the touch Sheet and hover doesn't exist.
          className="absolute end-1.5 top-1.5 size-6 p-0 opacity-0 transition-opacity [&_svg]:size-3.5 focus-visible:opacity-100 group-hover:opacity-100 data-[state=open]:opacity-100 max-lg:opacity-100"
        >
          <MoreHorizontal aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-44">
        <DropdownMenuItem
          data-testid="session-rail-make-primary"
          disabled={remap.isPending}
          onSelect={(e) => {
            e.preventDefault();
            remap.mutate(sessionId);
          }}
        >
          <Pin aria-hidden />
          {remap.isPending ? "Pinning…" : "Make primary"}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

// ─── Pure helpers ────────────────────────────────────────────────────────────

/** Index the live snapshot: session id → its live activity, workspace id → its
 *  full activity view (so a row can read the live ±diff-line counts too). Both
 *  let a rail row show the freshest state without a second stream. */
function buildSnapshotIndex(snapshot: DashboardSnapshotView | null): {
  liveActivity: Map<string, AgentActivityView>;
  wsById: Map<string, WorkspaceActivityView>;
} {
  const liveActivity = new Map<string, AgentActivityView>();
  const wsById = new Map<string, WorkspaceActivityView>();
  for (const p of snapshot?.projects ?? []) {
    for (const w of p.workspaces) {
      wsById.set(w.state.id, w);
      for (const s of w.sessions) liveActivity.set(s.session.session_id, s.activity);
    }
  }
  return { liveActivity, wsById };
}

function isAttention(activity: AgentActivityView | null): boolean {
  if (!activity) return false;
  return activity.needs_attention || ATTENTION_STATES.has(activity.state);
}

function matchesQuery(s: SessionSummaryView, q: string): boolean {
  if (!q) return true;
  return [s.title, s.first_prompt, s.last_prompt, s.workspace_title, s.git_branch]
    .filter(Boolean)
    .some((v) => (v as string).toLowerCase().includes(q));
}

/** The one ordering rule: newest activity first. A null timestamp sinks. */
function byModifiedDesc(a: RailEntry, b: RailEntry): number {
  return (b.session.modified_at ?? "").localeCompare(a.session.modified_at ?? "");
}
