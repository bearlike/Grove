/**
 * The fleet workstream's public surface.
 *
 * `FleetTree` is the rail's list, `FleetOverlays` the two app-level overlays
 * the shell mounts once, and `useFleetStream` the app's single subscription to
 * the daemon's event stream — the shell opens it so that "exactly once" is
 * structural rather than a convention the rail happens to honour.
 * `FleetDashboard` is the fleet's own route body. Everything else under this
 * directory is internal.
 */
export { FleetTree } from "./fleet-tree";
export { FleetOverlays } from "./fleet-overlays";
export { FleetDashboard } from "./fleet-dashboard";
export { useFleetStream, type FleetStream } from "./use-fleet";
