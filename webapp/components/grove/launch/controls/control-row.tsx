"use client";

import { useEffect, type ReactNode } from "react";

import { BranchPill } from "./branch-pill";
import { AgentPill } from "./agent-pill";
import { ModelPill } from "./model-pill";
import { ProjectPill } from "./project-pill";
import { RuntimePill } from "./runtime-pill";
import { WorkingDirectoryPill } from "./working-directory-pill";
import { LaunchPillGroup } from "../control-pill";
import { LAUNCH_TESTIDS, useLaunchControls } from "../launch-state";
import { useWorkspaceDefaults } from "@/lib/grove/hooks/launch";

/**
 * Every compact answer that defines a launch, in the row's fixed order.
 *
 * Six pills and no overflow. The overflow control held brief, base ref,
 * skip-init and save-as-defaults — four things nobody sets per create — and
 * charged the composer permanent width for them. They live in `More options`,
 * which opens the full form; a row that carries every knob is the 9-field
 * modal again, wearing a different shape.
 *
 * TWO ROWS, SPLIT BY MEANING RATHER THAN BY WIDTH. Six spelled-out values do
 * not fit one line, and the alternative this replaced was hiding three of them
 * behind bare glyphs. Free wrapping fixed the legibility and left the break
 * wherever the text happened to run out — on one project that orphaned a single
 * pill on line two, which is the awkwardness a wrap always eventually produces.
 *
 * So the break is authored: WHERE the work happens (project, directory,
 * branch), then WHO does it (agent, model, runtime). The rows keep those roles
 * whatever the labels are, so the composer's shape does not change when a
 * project's names get longer or its `agent_cwds` disappear. Each row still
 * wraps internally as a last resort on a narrow window.
 *
 * `ComposerToolbar`'s `items-end` keeps Send on the last line, so the corner it
 * lives in does not move.
 */
export function LaunchControlRow(): ReactNode {
  const { values, seed } = useLaunchControls();
  const defaults = useWorkspaceDefaults(values.repoRoot);

  useEffect(() => {
    const resolved = defaults.data;
    if (!resolved) return;
    seed({
      agentName: resolved.agent,
      runtime: resolved.runtime,
      brief: resolved.brief,
      model: resolved.model,
      branchMode: resolved.branch_mode,
      baseRef: resolved.base_ref,
      skipInit: resolved.skip_init,
    });
  }, [defaults.data, seed]);

  return (
    <LaunchPillGroup>
      <div
        className="flex min-w-0 flex-1 flex-col gap-1"
        data-testid={LAUNCH_TESTIDS.controls}
      >
        <div className="flex min-w-0 flex-wrap items-center gap-x-0.5 gap-y-1">
          <ProjectPill />
          <WorkingDirectoryPill />
          <BranchPill />
        </div>
        <div className="flex min-w-0 flex-wrap items-center gap-x-0.5 gap-y-1">
          <AgentPill />
          <ModelPill />
          <RuntimePill />
        </div>
      </div>
    </LaunchPillGroup>
  );
}
