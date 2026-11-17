"use client";

import { useMemo, type ReactNode } from "react";

import { EffortMark } from "@/components/grove/effort-icon";
import { LaunchPill, type LaunchPillOption } from "../control-pill";
import { useLaunchControls, useLaunchProblem } from "../launch-state";
import { effortLabel } from "@/lib/grove/adapters/launch";
import { useAgents, useWorkspaceDefaults } from "@/lib/grove/hooks";

// Picker-only, never sent: the levels are lowercase words, so a leading dash
// cannot collide with one, the same rule the model pill's Custom row follows.
const AGENT_DEFAULT_OPTION = "-default";

/**
 * The reasoning effort the agent launches with.
 *
 * Absent for an agent that lists no levels rather than disabled: a control that
 * can never be enabled for this agent is noise, and the engine would refuse any
 * value it sent. "Agent default" is a value, exactly like the model pill's, and
 * it is what an omitted `effort` produces.
 */
export function EffortPill(): ReactNode {
  const { values, set } = useLaunchControls();
  const problem = useLaunchProblem("effort");
  const agents = useAgents(values.repoRoot);
  const defaults = useWorkspaceDefaults(values.repoRoot);
  // `defaults.agent` is the engine's resolved agent (#1093), never a guess.
  const agentName = values.agentName ?? defaults.data?.agent ?? null;
  const efforts = agents.data?.find((agent) => agent.name === agentName)?.efforts ?? [];
  const options = useMemo<readonly LaunchPillOption[]>(
    () => [
      {
        id: AGENT_DEFAULT_OPTION,
        label: "Agent default",
        description: "Let the agent choose",
      },
      ...efforts.map((effort) => ({
        id: effort,
        label: effortLabel(effort),
        icon: <EffortMark level={effort} />,
      })),
    ],
    [efforts],
  );

  if (efforts.length === 0) return null;

  return (
    <LaunchPill
      kind="effort"
      ariaLabel="Reasoning effort"
      value={values.effort ?? AGENT_DEFAULT_OPTION}
      options={options}
      fallbackLabel="Agent default"
      // A chosen level wears its own rung; the default keeps the control's brain.
      leading={values.effort ? <EffortMark level={values.effort} /> : undefined}
      error={problem}
      onSelect={(id) => set({ effort: id === AGENT_DEFAULT_OPTION ? null : id })}
    />
  );
}
