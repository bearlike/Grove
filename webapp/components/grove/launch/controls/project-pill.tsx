"use client";

import { useEffect, useMemo, useRef, type ReactNode } from "react";
import { useSearchParams } from "next/navigation";

import { LaunchPill, type LaunchPillOption } from "../control-pill";
import { useLaunchControls, type LaunchValues } from "../launch-state";
import { useFleetStream } from "@/components/grove/fleet/use-fleet";

const LAST_PROJECT_STORAGE_KEY = "grove.launch.last-project";

type ProjectChoice = {
  readonly repoRoot: string;
  readonly cwd: string;
  readonly label: string;
};

type ProjectSelection = Pick<ProjectChoice, "repoRoot" | "cwd">;

interface LaunchProjectResolution {
  readonly choices: readonly ProjectChoice[];
  readonly requestedCwd: string | null;
  readonly remembered: ProjectChoice | null;
}

/** Resolves a live launch project without turning an unavailable URL into a display value. */
export function resolveLaunchProject({
  choices,
  requestedCwd,
  remembered,
}: LaunchProjectResolution): ProjectChoice | null {
  if (choices.length === 0) return null;
  return choices.find((choice) => choice.cwd === requestedCwd)
    ?? choices.find(
      (choice) =>
        choice.repoRoot === remembered?.repoRoot && choice.cwd === remembered?.cwd,
    )
    ?? choices[0]
    ?? null;
}

/**
 * A project switch clears a prior directory choice from another repo.
 *
 * `projectCwd` holds ONLY what the user picked, and is cleared to `null` here
 * for every project — a path from the old repo is meaningless under the new
 * one. The project's own directory is remembered separately as
 * `selectedProjectCwd`, which is its identity rather than a form value.
 *
 * Keeping those two apart is what lets `buildCreateRequest` follow the same
 * `touched` convention as every other field: an untouched pill sends nothing
 * and the engine resolves the repo's own `agent_cwds.default`. Seeding the
 * project's path into `projectCwd` instead would look identical on screen and
 * silently make that default unreachable, since the engine only consults it
 * when nothing was specified.
 */
export function projectSelectionValues(
  project: ProjectSelection,
): Pick<LaunchValues, "repoRoot" | "projectCwd" | "selectedProjectCwd"> {
  return {
    repoRoot: project.repoRoot,
    projectCwd: null,
    selectedProjectCwd: project.cwd,
  };
}

function rememberedProject(): ProjectChoice | null {
  if (typeof window === "undefined") return null;
  try {
    const value: unknown = JSON.parse(window.localStorage.getItem(LAST_PROJECT_STORAGE_KEY) ?? "null");
    if (
      typeof value === "object" &&
      value !== null &&
      "repoRoot" in value &&
      "cwd" in value &&
      typeof value.repoRoot === "string" &&
      typeof value.cwd === "string"
    ) {
      return { repoRoot: value.repoRoot, cwd: value.cwd, label: "" };
    }
  } catch {
    // An older or malformed preference is no preference at all.
  }
  return null;
}

function rememberProject(project: ProjectChoice): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(
      LAST_PROJECT_STORAGE_KEY,
      JSON.stringify({ repoRoot: project.repoRoot, cwd: project.cwd }),
    );
  } catch {
    // Retaining an answer is a convenience; storage refusal must not block launch.
  }
}

/** The repository and working directory control at the start of the launch row. */
export function ProjectPill(): ReactNode {
  const { values, seed, set } = useLaunchControls();
  const stream = useFleetStream();
  const search = useSearchParams();
  const restoredProject = useRef<ProjectChoice | null | undefined>(undefined);
  const seededProject = useRef<string | null | undefined>(undefined);
  const projects = stream.snapshot?.projects ?? [];
  const options = useMemo<readonly LaunchPillOption[]>(
    () =>
      projects.map((project) => ({
        id: project.cwd,
        label: project.repo_name,
        description: project.cwd,
        keywords: [project.repo_root, project.cwd],
      })),
    [projects],
  );
  const choices = useMemo<readonly ProjectChoice[]>(
    () =>
      projects.map((project) => ({
        repoRoot: project.repo_root,
        cwd: project.cwd,
        label: project.repo_name,
      })),
    [projects],
  );
  const loading = stream.isPending && stream.snapshot === undefined;
  const disabledReason = loading
    ? undefined
    : choices.length === 0
      ? "No projects registered"
      : undefined;
  useEffect(() => {
    const requestedCwd = search.get("project");
    if (seededProject.current === requestedCwd || choices.length === 0) return;
    seededProject.current = requestedCwd;
    const selected = choices.find((choice) => choice.cwd === requestedCwd);
    // Seeding preserves a draft and explicit controls; an unknown URL value is
    // deliberately ignored instead of selecting the first project.
    if (selected) seed(projectSelectionValues(selected));
  }, [choices, search, seed]);

  useEffect(() => {
    if (restoredProject.current === undefined) restoredProject.current = rememberedProject();
    const requestedCwd = search.get("project");
    const requestedProject = choices.find((choice) => choice.cwd === requestedCwd);
    if (values.repoRoot !== null || choices.length === 0 || requestedProject) return;

    const selected = resolveLaunchProject({
      choices,
      requestedCwd,
      remembered: restoredProject.current,
    });
    if (selected) set(projectSelectionValues(selected));
  }, [choices, search, set, values.repoRoot]);

  return (
    <LaunchPill
      kind="project"
      ariaLabel="Project"
      // `selectedProjectCwd`, NOT `projectCwd`. The two used to be the same
      // field and are not any more: `projectCwd` is the WORKING DIRECTORY the
      // user picked, which the working-directory pill owns and sets to a
      // labelled path like `homelab/litellm`. Reading it here made this pill
      // show no selection the moment a project was chosen (the seed clears it
      // to null) and then match no project at all once a directory was picked
      // — so the project could not be changed again. This pill's identity is
      // the project's own cwd, which is what `selectedProjectCwd` carries.
      value={values.selectedProjectCwd}
      options={options}
      // The noun is the list's, not the vendored default's: filtering to
      // nothing said "No models found." in the Project picker, and the search
      // box called itself "Search models...".
      searchNoun="projects"
      fallbackLabel="Project"
      disabledReason={disabledReason}
      onSelect={(cwd) => {
        const selected = choices.find((choice) => choice.cwd === cwd);
        if (!selected) return;
        rememberProject(selected);
        // A directory selected under the previous project could name a different
        // location here, so the new project's own cwd becomes the fresh seed.
        set(projectSelectionValues(selected));
      }}
    />
  );
}
