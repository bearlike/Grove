"use client";

import { useMemo, type ReactNode } from "react";

import { AgentMark } from "@/components/grove/agent-mark";
import { LaunchPill, type LaunchPillOption } from "../control-pill";
import { useLaunchControls } from "../launch-state";
import { useWorkspaceDefaults } from "@/lib/grove/hooks/launch";
import { useAgents } from "@/lib/grove/hooks/queries";

/** The launch agent, with the selected agent's own brand mark. */
export function AgentPill(): ReactNode {
  const { values, set } = useLaunchControls();
  const agents = useAgents(values.repoRoot);
  const defaults = useWorkspaceDefaults(values.repoRoot);
  const options = useMemo<readonly LaunchPillOption[]>(
    () =>
      (agents.data ?? []).map((agent) => ({
        id: agent.name,
        label: agent.name,
        description: agent.description,
        icon: <AgentMark agentName={agent.name} />,
      })),
    [agents.data],
  );
  const selectedAgent = values.agentName ?? defaults.data?.agent ?? agents.data?.[0]?.name ?? null;
  // Every other control is scoped to a repo — agents, models, branches and the
  // defaults cascade all dispatch on it — so until a project is chosen there is
  // nothing truthful to offer. Say which choice is missing rather than opening
  // an empty menu.
  const disabledReason =
    values.repoRoot === null
      ? "Choose a project first"
      : !agents.isPending && agents.data?.length === 0
        ? "No agents configured for this project"
        : undefined;

  return (
    <LaunchPill
      kind="agent"
      ariaLabel="Agent"
      value={values.agentName}
      options={options}
      fallbackLabel={selectedAgent ?? "Agent"}
      disabledReason={disabledReason}
      leading={selectedAgent ? <AgentMark agentName={selectedAgent} /> : undefined}
      onSelect={(agentName) => set({ agentName })}
    />
  );
}
