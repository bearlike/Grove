import type { FleetRow } from "./types";

/** Pins only rank the visible rows; filtering and project membership stay intact. */
export function pinsFirst(rows: readonly FleetRow[], pins: readonly string[]): FleetRow[] {
  const ranks = new Map(pins.map((id, index) => [id, index]));
  return [...rows].sort((a, b) =>
    (ranks.get(a.workspace.state.id) ?? pins.length) -
    (ranks.get(b.workspace.state.id) ?? pins.length),
  );
}
