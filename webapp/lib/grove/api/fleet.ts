import type {
  SessionActivityView,
  SubagentActivityView,
  SubagentFleetView,
} from "./types";

/**
 * The fleet shapes the card reads, aliased from the generated schema.
 *
 * These were a hand-written mirror while the daemon contract was still being
 * built; they are aliases now, so a field that moves on the wire fails
 * `typecheck` here instead of drifting silently into the card.
 */
export type SubagentFleetData = SubagentFleetView;

export type SubagentFleetMember = SubagentActivityView;

export type SubagentFleetSession = SessionActivityView;
