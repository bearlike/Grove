"use client";
import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { AgentGlyph } from "@/lib/grove/agent-icon";
import { agentStateGlyph, agentStateLabel } from "@/lib/grove/agent-state-tokens";
import { RelativeTime } from "@/components/shared/relative-time";
import { TurnsView } from "@/components/workspace/turns-view";
import type { SessionSummaryView } from "@/lib/grove/types";

/**
 * The Sessions panel body on `/w/[id]` — every recorded agent session for the
 * workspace (newest-first, as the wire delivers them), each row expandable
 * into its conversation digest. One row expanded at a time, panel-local state:
 * the digest is an inline drill-down, not a navigation, so no new route.
 * TurnsView mounts only for the expanded row — mounting IS the fetch trigger
 * (its hook is enabled by existence), so a collapsed panel costs zero turn
 * requests.
 *
 * Test seam: `data-testid="sessions-panel"`, `"session-row"` + `data-session-id`,
 * `"session-provenance"`, and the "no recorded sessions" empty-state text.
 */
export function SessionsPanel({
  workspaceId,
  sessions,
  isLoading,
}: {
  workspaceId: string;
  sessions: SessionSummaryView[] | undefined;
  isLoading?: boolean;
}) {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  if (isLoading && !sessions) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="sessions-panel">
        Loading sessions…
      </p>
    );
  }
  if (!sessions || sessions.length === 0) {
    // Degrade, don't hide: an empty panel still tells the reader the feature
    // exists and this workspace simply has no transcript yet.
    return (
      <p className="text-sm text-muted-foreground" data-testid="sessions-panel">
        no recorded sessions
      </p>
    );
  }

  return (
    <ul className="flex flex-col" data-testid="sessions-panel">
      {sessions.map((s) => (
        <SessionRow
          key={s.session_id}
          workspaceId={workspaceId}
          session={s}
          expanded={expandedId === s.session_id}
          onToggle={() =>
            setExpandedId((cur) => (cur === s.session_id ? null : s.session_id))
          }
        />
      ))}
    </ul>
  );
}

function SessionRow({
  workspaceId,
  session: s,
  expanded,
  onToggle,
}: {
  workspaceId: string;
  session: SessionSummaryView;
  expanded: boolean;
  onToggle: () => void;
}) {
  const state = s.activity.state;
  // CSS-var fallback rule: a streamed state the client's enum predates would
  // otherwise resolve to no var at all — fall back to the neutral unknown tone.
  const stateColor = `var(--agent-${state}, var(--agent-unknown))`;
  const Chevron = expanded ? ChevronDown : ChevronRight;

  return (
    <li className="border-b border-border last:border-b-0">
      <button
        type="button"
        data-testid="session-row"
        data-session-id={s.session_id}
        aria-expanded={expanded}
        onClick={onToggle}
        className="flex w-full items-center gap-2.5 py-2.5 text-left transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
      >
        <Chevron aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
        <AgentGlyph
          agentName={s.adapter_kind}
          adapterKind={s.adapter_kind}
          className="size-4 shrink-0"
        />
        <div className="min-w-0 flex-1">
          <p className="line-clamp-1 text-sm text-foreground">
            {s.title ?? s.first_prompt ?? s.session_id}
          </p>
          <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 font-mono text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1" style={{ color: stateColor }}>
              <span aria-hidden>{agentStateGlyph(state)}</span>
              {agentStateLabel(state)}
            </span>
            <span>
              {s.activity.human_turns}t {s.activity.tool_calls}⚒
            </span>
            <span>
              {fmtTokens(s.activity.tokens_in)}↑ {fmtTokens(s.activity.tokens_out)}↓
            </span>
            {s.activity.model && <span className="truncate">{s.activity.model}</span>}
          </p>
        </div>
        <span
          data-testid="session-provenance"
          className="shrink-0 font-mono text-[10px] uppercase tracking-wider text-muted-foreground/70"
        >
          {provenanceLabel(s.provenance)}
        </span>
        <span className="shrink-0 text-[11px] text-muted-foreground">
          <RelativeTime iso={s.modified_at} />
        </span>
      </button>
      {expanded && (
        <div className="pl-6">
          <TurnsView workspaceId={workspaceId} sessionId={s.session_id} />
        </div>
      )}
    </li>
  );
}

/** Quiet human labels for the wire's provenance codes; unknown codes pass raw. */
function provenanceLabel(p: string): string {
  if (p === "grove_launched") return "grove";
  if (p === "fs_discovered") return "hand-started";
  return p;
}

function fmtTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}
