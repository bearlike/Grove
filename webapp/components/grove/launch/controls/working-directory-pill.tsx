"use client";

import { useMemo, type ReactNode } from "react";

import { LaunchPill, type LaunchPillOption } from "../control-pill";
import { useLaunchControls } from "../launch-state";
import { useWorkspaceDefaults } from "@/lib/grove/hooks/launch";

/**
 * An EXPLICIT "start at the repo root", distinct from having chosen nothing.
 *
 * `null` on `projectCwd` means *not specified*, which the engine answers by
 * resolving the repo's own `agent_cwds.default`. So root cannot also be `null`
 * — a user who deliberately picked the root would silently get the configured
 * default instead, which is the opposite of what they asked for. `"."` anchors
 * on the repo root and resolves to the empty subpath, the historical shape.
 */
const REPOSITORY_ROOT = ".";

/** The directory within the new worktree where the selected agent starts. */
export function WorkingDirectoryPill(): ReactNode {
  const { values, set } = useLaunchControls();
  const defaults = useWorkspaceDefaults(values.repoRoot);
  const configured = defaults.data?.agent_cwds ?? [];
  const nestedProjectCwd =
    values.selectedProjectCwd !== null &&
    values.repoRoot !== null &&
    values.selectedProjectCwd !== values.repoRoot
      ? values.selectedProjectCwd.startsWith(`${values.repoRoot}/`)
        ? values.selectedProjectCwd.slice(values.repoRoot.length + 1)
        : values.selectedProjectCwd
      : null;
  const options = useMemo<readonly LaunchPillOption[]>(
    () => [
      { id: REPOSITORY_ROOT, label: "Repository root" },
      ...configured.map(({ label, path }) => ({ id: path, label })),
      ...(nestedProjectCwd !== null && !configured.some(({ path }) => path === nestedProjectCwd)
        ? [{ id: nestedProjectCwd, label: nestedProjectCwd }]
        : []),
    ],
    [configured, nestedProjectCwd],
  );

  if (configured.length === 0 && nestedProjectCwd === null) return null;

  const fallbackPath = defaults.data?.agent_cwd ?? nestedProjectCwd;
  const selectedPath = values.projectCwd ?? fallbackPath;

  return (
    <LaunchPill
      // NOT `project` — see `LaunchPillKind`. The kind is the row's open-menu
      // key, so sharing one with `ProjectPill` opened both popovers at once and
      // left this list unreachable underneath the other.
      kind="directory"
      ariaLabel="Working directory"
      value={values.projectCwd}
      options={options}
      fallbackLabel={
        selectedPath === null || selectedPath === REPOSITORY_ROOT
          ? "Repository root"
          : options.find((option) => option.id === selectedPath)?.label ?? selectedPath
      }
      disabledReason={values.repoRoot === null ? "Choose a project first" : undefined}
      // The label is presentation only; path is the stable wire value, so a
      // project may rename a label without changing an existing workspace.
      onSelect={(path) => set({ projectCwd: path })}
    />
  );
}
