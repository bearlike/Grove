"use client";

import { useMemo, type ReactNode } from "react";

import { LaunchPill, type LaunchPillOption } from "../control-pill";
import { useLaunchControls } from "../launch-state";
import { useWorkspaceDefaults } from "@/lib/grove/hooks/launch";

/** The id the "let the hub pick" option carries; a node name never starts with `:`. */
const ANY_NODE = ":any";

/**
 * Which connected Grove daemon runs the workspace, on a federation hub.
 *
 * Rendered only when `targets` names a node besides this daemon, which is the
 * hub case; everywhere else there is no choice to offer. Picking a node also
 * narrows the runtime pill to that node's own runtimes, and a runtime chosen
 * for another node is cleared rather than sent somewhere that lacks it.
 */
export function NodePill(): ReactNode {
  const { values, set } = useLaunchControls();
  const defaults = useWorkspaceDefaults(values.repoRoot);
  const nodes = useMemo(
    () => (defaults.data?.targets ?? []).flatMap((target) => (target.node ? [target] : [])),
    [defaults.data?.targets],
  );
  const options = useMemo<readonly LaunchPillOption[]>(
    () => [
      { id: ANY_NODE, label: "Any node", description: "The hub places it where it fits" },
      ...nodes.map((target) => ({
        id: target.node as string,
        label: target.node as string,
        description: target.runtimes?.length ? target.runtimes.join(", ") : "Host and container",
      })),
    ],
    [nodes],
  );
  if (nodes.length === 0) return null;

  return (
    <LaunchPill
      kind="node"
      ariaLabel="Node"
      value={values.node ?? ANY_NODE}
      options={options}
      fallbackLabel={values.node ?? "Any node"}
      disabledReason={values.repoRoot === null ? "Choose a project first" : undefined}
      onSelect={(id) => {
        const node = id === ANY_NODE ? null : id;
        const runtimes = nodes.find((target) => target.node === node)?.runtimes ?? [];
        const keepRuntime =
          values.runtime === null ||
          values.runtime === "host" ||
          values.runtime === "container" ||
          runtimes.includes(values.runtime);
        set(keepRuntime ? { node } : { node, runtime: null });
      }}
    />
  );
}
