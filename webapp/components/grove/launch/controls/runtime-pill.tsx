"use client";

import { useMemo, type ReactNode } from "react";

import { runtimeGlyph, runtimeLabel } from "@/components/grove/fleet/tokens";
import { LaunchPill, type LaunchPillOption } from "../control-pill";
import { type RuntimeChoice, useLaunchControls } from "../launch-state";
import { useWorkspaceDefaults } from "@/lib/grove/hooks/launch";

const RUNTIMES: readonly RuntimeChoice[] = ["host", "container"];

/** The isolation runtime the workspace will use. */
export function RuntimePill(): ReactNode {
  const { values, set } = useLaunchControls();
  const defaults = useWorkspaceDefaults(values.repoRoot);
  const options = useMemo<readonly LaunchPillOption[]>(
    () =>
      RUNTIMES.map((runtime) => {
        const Glyph = runtimeGlyph(runtime);
        return { id: runtime, label: runtimeLabel(runtime), icon: <Glyph aria-hidden /> };
      }),
    [],
  );
  const selectedRuntime = values.runtime ?? defaults.data?.runtime ?? "host";
  const ActiveGlyph = runtimeGlyph(selectedRuntime);

  return (
    <LaunchPill
      kind="runtime"
      ariaLabel="Runtime"
      value={values.runtime}
      options={options}
      fallbackLabel={runtimeLabel(selectedRuntime)}
      disabledReason={values.repoRoot === null ? "Choose a project first" : undefined}
      // The mark is per VALUE here, not per control — host and container are
      // two different glyphs — so it cannot come from the pill's own table.
      leading={<ActiveGlyph aria-hidden />}
      onSelect={(runtime) => set({ runtime: runtime as RuntimeChoice })}
    />
  );
}
