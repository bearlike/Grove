import type { AgentSummaryView } from "@/lib/grove/api";

type Kind = AgentSummaryView["kind"];

/**
 * The engine's capability answer per kind, as #1093 documents it, for FIXTURES
 * ONLY. App code must read `launch_modes`/`runtimes` off the wire and never
 * restate this table: the engine owns it, and a client copy is exactly the
 * hard-coded kind list the contract replaced.
 */
const LAUNCH_MODES: Readonly<Record<Kind, AgentSummaryView["launch_modes"]>> = {
  claude_code: ["native", "terminal"],
  codex: ["native", "terminal"],
  opencode: ["native", "terminal"],
  generic: ["terminal"],
  mewbo: ["terminal"],
};

/** A roster entry shaped as the daemon sends it. */
export function agentSummary(
  name: string,
  kind: Kind,
  overrides: Partial<AgentSummaryView> = {},
): AgentSummaryView {
  const launch_modes = overrides.launch_modes ?? LAUNCH_MODES[kind];
  return {
    name,
    kind,
    description: "",
    native: launch_modes.includes("native"),
    models: [],
    launch_modes,
    // `remote` exactly where native is a launch mode: a remote runtime drives
    // the agent over its own protocol.
    runtimes: launch_modes.includes("native") ? ["host", "container", "remote"] : ["host", "container"],
    ...overrides,
  };
}
