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
  // NO `?? "host"` FALLBACK. A pill on this surface exists to say what an
  // untouched create will actually do, and the request omits an untouched
  // field so the engine can resolve it — so inventing a value here is the pill
  // making a promise nothing keeps. It made exactly one: `container.enabled`
  // defaults to TRUE, so every render before `/defaults` answered claimed Host
  // and every create in that window produced a container. Submitting is one
  // keypress and this is the first control on the page; that window is real.
  const selectedRuntime = values.runtime ?? defaults.data?.runtime ?? null;
  const ActiveGlyph = selectedRuntime === null ? null : runtimeGlyph(selectedRuntime);

  return (
    <LaunchPill
      kind="runtime"
      ariaLabel="Runtime"
      value={values.runtime}
      options={options}
      fallbackLabel={selectedRuntime === null ? "Runtime" : runtimeLabel(selectedRuntime)}
      disabledReason={values.repoRoot === null ? "Choose a project first" : undefined}
      // The mark is per VALUE here, not per control — host and container are
      // two different glyphs — so it cannot come from the pill's own table, and
      // there is none to draw until the cascade has named a value.
      leading={ActiveGlyph === null ? undefined : <ActiveGlyph aria-hidden />}
      onSelect={(runtime) => set({ runtime: runtime as RuntimeChoice })}
    />
  );
}
