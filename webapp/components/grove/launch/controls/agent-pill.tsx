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
import { LAUNCH_TESTIDS, useLaunchControls } from "../launch-state";
import { useWorkspaceDefaults } from "@/lib/grove/hooks/launch";
import { useAgents } from "@/lib/grove/hooks/queries";
import type { AgentSummaryView } from "@/lib/grove/api";

/** The kinds whose launch has two modes; every other kind has only its terminal. */
const NATIVE_KINDS: ReadonlySet<AgentSummaryView["kind"]> = new Set(["claude_code", "codex"]);

/** Which channel Grove drives the agent over, as the row's second line. */
export function sessionModeLabel(native: boolean): string {
  return native ? "Native session" : "Terminal";
}

/**
 * The row's second line says which channel the workspace will be driven over
 * BY DEFAULT for that entry — a native session Grove owns (interrupt, model
 * switch and answers ride the provider's protocol) against the interactive
 * terminal typed at. The operator's own description leads when there is one;
 * the mode is stated either way, because two entries for one provider (a
 * gateway profile and a plain one, say) otherwise read as the same thing.
 * The panel below the list is where a create overrides it.
 */
export function agentDescription(agent: AgentSummaryView): string {
  const mode = sessionModeLabel(agent.native);
  return agent.description ? `${agent.description} · ${mode}` : mode;
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
  const agents = useAgents(values.repoRoot);
  const defaults = useWorkspaceDefaults(values.repoRoot);
  const options = useMemo<readonly LaunchPillOption[]>(
    () =>
      (agents.data ?? []).map((agent) => ({
        id: agent.name,
        label: agent.name,
        description: agentDescription(agent),
        icon: <AgentMark agentName={agent.name} />,
      })),
    [agents.data],
  );
  const selectedAgent = values.agentName ?? defaults.data?.agent ?? agents.data?.[0]?.name ?? null;
  const selectedSpec = agents.data?.find((agent) => agent.name === selectedAgent) ?? null;
  const hasMode = selectedSpec !== null && NATIVE_KINDS.has(selectedSpec.kind);
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
      value={values.agentName}
      options={options}
      // A host configures a handful of agents, so there is nothing to narrow —
      // and an unsearchable list still gets a keyboard anchor named after THIS
      // control rather than the vendored default's hard-coded "Model".
      fallbackLabel={selectedAgent ?? "Agent"}
      disabledReason={disabledReason}
      leading={selectedAgent ? <AgentMark agentName={selectedAgent} /> : undefined}
      onSelect={(agentName) => set({ agentName })}
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
