"use client";

import type { ReactNode } from "react";

import { BranchPill } from "./branch-pill";
import { AgentPill } from "./agent-pill";
import { ProjectPill } from "./project-pill";
import { NodePill } from "./node-pill";
import { RuntimePill } from "./runtime-pill";
import { WorkingDirectoryPill } from "./working-directory-pill";
import { ProfilePill } from "./profile-pill";
import { LAUNCH_TESTIDS } from "../launch-state";

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
 * The approved order starts with the project context, then where it runs, then
 * the tool and profile. Each group is separated by spacing only, so the shelf
 * stays one quiet surface.
 *
 * The cascade's answers are seeded by `LaunchStateProvider`, not here: the
 * More options dialog composes the same state without this row's surface.
 */
export function LaunchControlRow(): ReactNode {
  return (
    <div className="flex min-w-0 flex-1 flex-wrap items-center gap-3" data-testid={LAUNCH_TESTIDS.controls}>
      <div className="flex flex-wrap items-center gap-x-0.5 gap-y-1">
        <ProjectPill />
        <WorkingDirectoryPill />
        <BranchPill />
      </div>
      <div className="flex flex-wrap items-center gap-x-0.5 gap-y-1">
        <NodePill />
        <RuntimePill />
      </div>
      <div className="flex flex-wrap items-center gap-x-0.5 gap-y-1">
        <AgentPill />
        <ProfilePill />
      </div>
    </div>
  );
}
