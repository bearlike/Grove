"use client";

import { useMemo, type ReactNode } from "react";

import { AgentMark } from "@/components/grove/agent-mark";
import { LaunchPill, type LaunchPillOption } from "../control-pill";
import { useLaunchControls } from "../launch-state";
import { currentPreview } from "@/lib/grove/adapters/launch";
import { useWorkspaceDefaults } from "@/lib/grove/hooks/launch";
import { agentTools, profileConflict, runtimeKindOf } from "@/lib/grove/adapters/launch-flow";
import { useAgents } from "@/lib/grove/hooks/queries";

/**
 * Each profile's name without the words every profile of its tool repeats.
 *
 * The Agent pill beside this one already names the tool, so a menu reading
 * "Claude Code (default)", "Claude Code (via Example Gateway)" says "Claude
 * Code" four times and truncates the part that differs. The shared lead is
 * read off the names themselves rather than off the tool's display word, which
 * an operator's profile names need not start with. A parenthetical left over
 * after the cut is unwrapped; a name that would become empty keeps its whole.
 */
export function profileLabels(names: readonly string[]): readonly string[] {
  if (names.length < 2) return names;
  const words = names.map((name) => name.split(" "));
  let shared = 0;
  while (words.every((parts) => parts.length > shared + 1 && parts[shared] === words[0]![shared])) {
    shared += 1;
  }
  return names.map((name, index) => {
    const rest = words[index]!.slice(shared).join(" ").trim();
    const unwrapped = rest.startsWith("(") && rest.endsWith(")") ? rest.slice(1, -1).trim() : rest;
    return unwrapped === "" ? name : unwrapped;
  });
}

/**
 * Which configured entry of the chosen tool runs the brief — a gateway profile
 * against the plain one, say.
 *
 * Conditional by design: a tool with one entry has no question to ask, so the
 * pill is absent rather than offering a single choice. A profile IS a roster
 * entry, so the value it sets is `agentName` itself, the identity the create
 * already sends; there is no second "profile" field to drift from it. A row
 * carries no description unless it cannot run on the chosen runtime: the
 * shortened name is the whole answer, and a second line only adds reading.
 */
export function ProfilePill(): ReactNode {
  const state = useLaunchControls();
  const { values, set } = state;
  const agents = useAgents(values.repoRoot);
  const offered = useMemo(() => agents.data ?? [], [agents.data]);
  const preview = currentPreview(state);
  const selected = offered.find((agent) => agent.name === (preview?.agent_name ?? values.agentName)) ?? null;
  const tool = useMemo(
    () => agentTools(offered).find((candidate) => candidate.kind === (values.agentKind ?? selected?.kind)) ?? null,
    [offered, selected?.kind, values.agentKind],
  );
  const profiles = useMemo(() => tool?.profiles ?? [], [tool]);
  const labels = useMemo(() => profileLabels(profiles.map((profile) => profile.name)), [profiles]);
  // The runtime this create will use, so each entry can say up front whether
  // it runs there. Presentation only: the preview's problem is what refuses.
  const defaults = useWorkspaceDefaults(values.repoRoot);
  const runtime = values.runtime ?? defaults.data?.runtime ?? null;
  const runtimeKind = runtime === null ? null : runtimeKindOf(runtime);
  const options = useMemo<readonly LaunchPillOption[]>(
    () =>
      profiles.map((profile, index) => {
        const conflict = profileConflict(profile, runtimeKind);
        return {
          id: profile.name,
          label: labels[index]!,
          ...(conflict === null ? {} : { description: conflict }),
          icon: <AgentMark agentName={profile.name} />,
        };
      }),
    [profiles, labels, runtimeKind],
  );

  if (profiles.length < 2) return null;

  const current = preview?.agent_name ?? values.agentName;
  const currentLabel = labels[profiles.findIndex((profile) => profile.name === current)];

  return (
    <LaunchPill
      kind="profile"
      ariaLabel="Profile"
      value={current}
      options={options}
      fallbackLabel={currentLabel ?? current ?? "Profile"}
      onSelect={(agentName) => set({ agentName })}
    />
  );
}
