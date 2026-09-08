"use client";

import { useEffect, type ReactNode } from "react";

import { BranchPill } from "./branch-pill";
import { AgentPill } from "./agent-pill";
import { ProjectPill } from "./project-pill";
import { RuntimePill } from "./runtime-pill";
import { WorkingDirectoryPill } from "./working-directory-pill";
import { LAUNCH_TESTIDS, useLaunchControls } from "../launch-state";
import { useWorkspaceDefaults } from "@/lib/grove/hooks/launch";

/**
 * What the workspace will BE, on one shelf under the writing surface.
 *
 * The split this row now sits on either side of is between writing and
 * configuring. Project, directory, branch and runtime describe the workspace
 * the brief will run in and belong together, away from the editor; the model
 * pill left for the composer's own toolbar, beside send, because it configures
 * the agent that reads the message rather than the workspace that holds it.
 *
 * ONE ROW, NOT THE TWO AUTHORED ONES THIS REPLACED. The previous split (where
 * the work happens, then who does it) existed because six spelled-out values
 * could not fit a line inside the composer bar. Outside it, and one pill
 * lighter, four fit — and the shelf is free to wrap on a narrow window without
 * orphaning anything, because there is no second row for a pill to fall out of.
 *
 * `RuntimePill` is pushed to the far end: host-versus-container is the answer
 * that changes what every pill beside it MEANS — a directory inside a container
 * is not the same place as the same path on the host — so it reads as the
 * shelf's qualifier rather than as a fifth peer.
 *
 * The seed effect stays here, and it is the reason this file is not just
 * markup: it is the single caller of `seed`, and its dependency list is the
 * fix for the render loop `launchReducer` documents.
 */
export function LaunchControlRow(): ReactNode {
  const { values, seed } = useLaunchControls();
  const defaults = useWorkspaceDefaults(values.repoRoot);

  useEffect(() => {
    const resolved = defaults.data;
    if (!resolved) return;
    // `model` is seeded from here even though its pill moved: the default is
    // per-agent (`resolve_models`) and arrives in this one response, so
    // trimming the seed to "the fields this file still draws" would leave the
    // toolbar's pill permanently empty.
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
    <div
      className="flex min-w-0 flex-1 flex-wrap items-center gap-x-0.5 gap-y-1"
      data-testid={LAUNCH_TESTIDS.controls}
    >
      <ProjectPill />
      <WorkingDirectoryPill />
      <AgentPill />
      <BranchPill />
      <div className="ms-auto flex min-w-0 items-center">
        <RuntimePill />
      </div>
    </div>
  );
}
