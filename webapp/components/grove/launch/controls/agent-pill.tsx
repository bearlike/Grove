"use client";

import { useMemo, type ReactNode } from "react";

import { AgentMark } from "@/components/grove/agent-mark";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { LaunchPill, type LaunchPillOption } from "../control-pill";
import { agentTools } from "@/lib/grove/adapters/launch-flow";
import { LAUNCH_TESTIDS, useLaunchControls, useLaunchProblem } from "../launch-state";
import { useWorkspaceDefaults } from "@/lib/grove/hooks/launch";
import { useAgents } from "@/lib/grove/hooks/queries";
import type { AgentSummaryView } from "@/lib/grove/api";

/** Which channel Grove drives the agent over, as the row's session-mode control says. */
export function sessionModeLabel(native: boolean): string {
  return native ? "Native session" : "Terminal";
}

/** A tool's menu rows need no repeated transport suffix or long secondary copy. */
export function agentDescription(agent: AgentSummaryView): string | undefined {
  const words = agent.description?.trim().split(/\s+/).filter(Boolean) ?? [];
  return words.length === 0 ? undefined : words.slice(0, 5).join(" ");
}

/**
 * The launch agent, with the selected agent's own brand mark — and, for a
 * provider with a native protocol, the SESSION MODE this create will use.
 *
 * The mode is a panel field on this pill rather than a second roster entry
 * per profile: a person with three Claude profiles wants each of them
 * runnable either way without a config edit, and the entry's own `native` is
 * only the default. An untouched field shows that default (the same
 * "display what will happen" rule the runtime pill follows), a touched one
 * rides the request as `native`, and switching agents forgets the choice
 * because the default belongs to the entry (`launch-state` RULE 1b).
 */
export function AgentPill(): ReactNode {
  const { values, set } = useLaunchControls();
  const problem = useLaunchProblem("agentName");
  const agents = useAgents(values.repoRoot);
  const defaults = useWorkspaceDefaults(values.repoRoot);
  const offered = useMemo(() => agents.data ?? [], [agents.data]);
  // One row per TOOL, grouped from the roster the engine returned; the entry
  // (profile) within a tool is the ProfilePill's question, and only appears
  // when a tool has more than one. `agent_name` stays the identity sent.
  const tools = useMemo(() => agentTools(offered), [offered]);
  const options = useMemo<readonly LaunchPillOption[]>(
    () =>
      tools.map((tool) => {
        const only = tool.profiles.length === 1 ? tool.profiles[0]! : null;
        return {
          id: tool.kind,
          label: tool.label,
          description: only
            ? agentDescription(only)
            : `${tool.profiles.length} configurations`,
          icon: <AgentMark agentName={tool.profiles[0]!.name} />,
        };
      }),
    [tools],
  );
  // NO `?? agents.data?.[0]`: the first roster entry is the engine's answer
  // only once it says so (#1093 resolves it); until then the seeded saved
  // agent is the one honest value, and nothing is shown before it arrives.
  const selectedAgent = values.agentName ?? defaults.data?.agent ?? null;
  const selectedSpec = agents.data?.find((agent) => agent.name === selectedAgent) ?? null;
  const selectedTool = tools.find((tool) => tool.kind === (values.agentKind ?? selectedSpec?.kind)) ?? null;
  // The ENTRY's own launch modes (#1093), never a client list of kinds: the
  // panel exists only where the engine offers both.
  const hasMode = selectedSpec !== null && selectedSpec.launch_modes.length > 1;
  // The entry's default is the resolved answer for an untouched field; a
  // touched one is the person's. `null` while the catalog has not answered.
  const nativeResolved =
    values.native ?? (selectedSpec === null ? null : selectedSpec.native);
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
      value={selectedTool?.kind ?? null}
      options={options}
      // A host configures a handful of agents, so there is nothing to narrow —
      // and an unsearchable list still gets a keyboard anchor named after THIS
      // control rather than the vendored default's hard-coded "Model".
      fallbackLabel={selectedTool?.label ?? selectedAgent ?? "Agent"}
      disabledReason={disabledReason}
      error={problem}
      leading={selectedAgent ? <AgentMark agentName={selectedAgent} /> : undefined}
      onSelect={(kind) => {
        if (kind !== values.agentKind) set({ agentKind: kind as NonNullable<typeof values.agentKind> });
      }}
    >
      {hasMode && nativeResolved !== null ? (
        <div className="flex flex-col gap-2">
          <label htmlFor={LAUNCH_TESTIDS.sessionMode} className="text-xs text-content-tertiary">
            Session mode
          </label>
          <Select
            value={nativeResolved ? "native" : "terminal"}
            onValueChange={(choice) => set({ native: choice === "native" })}
          >
            <SelectTrigger
              id={LAUNCH_TESTIDS.sessionMode}
              className="w-full"
              data-testid={LAUNCH_TESTIDS.sessionMode}
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="native">
                {sessionModeLabel(true)} — headless; Grove holds the control channel
              </SelectItem>
              <SelectItem value="terminal">
                {sessionModeLabel(false)} — the agent's interactive UI in the pane
              </SelectItem>
            </SelectContent>
          </Select>
          <p className="text-xs text-content-tertiary">
            {values.native === null
              ? `This entry's default. Interrupt, model switch and answers ${nativeResolved ? "go to the agent's own protocol" : "are typed into the terminal"}.`
              : "Chosen for this create; the entry's default is unchanged."}
          </p>
        </div>
      ) : null}
    </LaunchPill>
  );
}
