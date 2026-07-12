import type { SessionActivityView } from "./types";

/**
 * One session in the fleet hierarchy plus its itemized children — the pure
 * grouping layer over `WorkspaceActivityView.sessions` (#173's flat wire
 * list: every entry, primary or sub-agent, carries its own
 * `AgentSessionView.parent_session_id`, `null` for a top-level session).
 *
 * Built generically (a session's `children` can themselves carry children)
 * even though today's only producer (`ClaudeCodeAdapter.fleet_activity`)
 * attributes every sidechain thread directly to the PRIMARY session — one
 * level deep, never a grandchild — so this stays correct for free if a
 * future adapter or a deeper lineage ever populates a fleet member's own
 * `parent_session_id` with another fleet member's id.
 */
export interface FleetNode {
  entry: SessionActivityView;
  children: FleetNode[];
}

/**
 * Group a workspace's flat `sessions` list into root sessions (no parent, or
 * a parent id not present in this same list — defensive against a partial
 * snapshot) each carrying their own nested fleet members. Order is
 * preserved from the input at every level (the engine already emits
 * primary-first, extras next, each followed by its own fleet, #173).
 */
export function buildFleetTree(sessions: SessionActivityView[]): FleetNode[] {
  const bySessionId = new Map(sessions.map((s) => [s.session.session_id, s]));
  const childrenOf = new Map<string, SessionActivityView[]>();
  const roots: SessionActivityView[] = [];
  for (const s of sessions) {
    const parentId = s.session.parent_session_id;
    if (parentId && bySessionId.has(parentId)) {
      const list = childrenOf.get(parentId);
      if (list) list.push(s);
      else childrenOf.set(parentId, [s]);
    } else {
      roots.push(s);
    }
  }
  const build = (s: SessionActivityView): FleetNode => ({
    entry: s,
    children: (childrenOf.get(s.session.session_id) ?? []).map(build),
  });
  return roots.map(build);
}

/** Total itemized fleet members across every root — the "is there anything
 *  to show" gate the work panel uses to pick tree-vs-plain-count rendering. */
export function fleetMemberCount(roots: FleetNode[]): number {
  let count = 0;
  const walk = (nodes: FleetNode[]) => {
    for (const n of nodes) {
      count += n.children.length;
      walk(n.children);
    }
  };
  walk(roots);
  return count;
}
