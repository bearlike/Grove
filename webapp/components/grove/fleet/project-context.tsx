"use client";

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { CheckIcon, ChevronsUpDownIcon, FolderGit2Icon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import type { FleetRow, ProjectGroup } from "./types";

const PROJECT_CONTEXT_STORAGE_KEY = "grove.fleet.project-context";

export type ProjectContext =
  | { readonly kind: "all" }
  | { readonly kind: "selected"; readonly cwd: string; readonly project: ProjectGroup }
  | { readonly kind: "missing"; readonly cwd: string };

/** Resolves a saved project identity without treating a missing project as All. */
export function projectContextOf(
  selectedProjectCwd: string | null,
  projects: readonly ProjectGroup[],
): ProjectContext {
  if (selectedProjectCwd === null) return { kind: "all" };
  const project = projects.find((candidate) => candidate.cwd === selectedProjectCwd);
  return project
    ? { kind: "selected", cwd: selectedProjectCwd, project }
    : { kind: "missing", cwd: selectedProjectCwd };
}

/**
 * Narrows an already-sorted fleet by the selected snapshot group's membership.
 *
 * `repo_root` would combine sibling configured projects that share a repository;
 * the daemon already files each workspace under one `ProjectGroupView`, so its
 * workspace ids are the only scope that preserves that configured identity.
 */
export function scopeFleetRows(
  rows: readonly FleetRow[],
  context: ProjectContext,
): FleetRow[] {
  if (context.kind === "all") return [...rows];
  if (context.kind === "missing") return [];
  const ids = new Set(context.project.workspaces.map((workspace) => workspace.state.id));
  return rows.filter((row) => ids.has(row.workspace.state.id) && row.repoRoot === context.project.repo_root);
}

/** The existing landing route, optionally seeded with one configured project identity. */
export function projectLaunchHref(context: ProjectContext): string {
  return context.kind === "selected" ? `/?project=${encodeURIComponent(context.cwd)}` : "/";
}

function pathParts(path: string): string[] {
  return path.split("/").filter(Boolean);
}

function shortestDistinctSuffix(path: string, peers: readonly string[]): string {
  const parts = pathParts(path);
  for (let width = 1; width <= parts.length; width += 1) {
    const suffix = parts.slice(-width).join("/");
    if (peers.filter((peer) => pathParts(peer).slice(-width).join("/") === suffix).length === 1) {
      return suffix;
    }
  }
  return parts.join("/");
}

/** A visible name and, only where necessary, a non-absolute configured identity. */
export function projectContextLabel(
  project: ProjectGroup,
  projects: readonly ProjectGroup[],
): string {
  const peers = projects.filter((candidate) => candidate.repo_name === project.repo_name);
  if (peers.length === 1) return project.repo_name;
  return `${project.repo_name} — ${shortestDistinctSuffix(
    project.cwd,
    peers.map((peer) => peer.cwd),
  )}`;
}

function contextLabel(context: ProjectContext, projects: readonly ProjectGroup[]): string {
  if (context.kind === "all") return "All projects";
  if (context.kind === "missing") return "Project unavailable";
  return projectContextLabel(context.project, projects);
}

function projectOptionValue(project: ProjectGroup, projects: readonly ProjectGroup[]): string {
  return `${projectContextLabel(project, projects)} ${project.cwd}`;
}

function readStoredProject(): string | null {
  try {
    const value = window.sessionStorage.getItem(PROJECT_CONTEXT_STORAGE_KEY);
    return value === null || value === "" ? null : value;
  } catch {
    return null;
  }
}

function writeStoredProject(cwd: string | null): void {
  try {
    if (cwd === null) window.sessionStorage.removeItem(PROJECT_CONTEXT_STORAGE_KEY);
    else window.sessionStorage.setItem(PROJECT_CONTEXT_STORAGE_KEY, cwd);
  } catch {
    // Context is a visit convenience; a storage refusal must leave the rail usable.
  }
}

export interface ProjectContextController {
  readonly selectedProjectCwd: string | null;
  readonly context: ProjectContext;
  readonly selectedProject: ProjectGroup | null;
  readonly selectProject: (cwd: string | null) => void;
}

/** Visit-scoped rail context; server data stays in the fleet query cache. */
export function useProjectContext(
  projects: readonly ProjectGroup[],
): ProjectContextController {
  const [selectedProjectCwd, setSelectedProjectCwd] = useState<string | null>(null);
  const restored = useRef(false);

  useEffect(() => {
    if (restored.current) return;
    restored.current = true;
    setSelectedProjectCwd(readStoredProject());
  }, []);

  const selectProject = useCallback((cwd: string | null) => {
    setSelectedProjectCwd(cwd);
    writeStoredProject(cwd);
  }, []);
  const context = useMemo(
    () => projectContextOf(selectedProjectCwd, projects),
    [projects, selectedProjectCwd],
  );

  return {
    selectedProjectCwd,
    context,
    selectedProject: context.kind === "selected" ? context.project : null,
    selectProject,
  };
}

/** Native searchable project selector for the sidebar's visit-scoped context. */
export function ProjectContextPicker({
  projects,
  context,
  onSelect,
  status = "ready",
}: {
  readonly projects: readonly ProjectGroup[];
  readonly context: ProjectContext;
  readonly onSelect: (cwd: string | null) => void;
  readonly status?: "ready" | "loading" | "error";
}): React.ReactNode {
  const [open, setOpen] = useState(false);
  const listId = useId();
  const trigger = useRef<HTMLButtonElement | null>(null);
  const label = status === "loading" && context.kind === "missing"
    ? "Loading projects"
    : contextLabel(context, projects);
  const options = useMemo(
    () => projects.map((project) => ({ project, label: projectContextLabel(project, projects) })),
    [projects],
  );
  const choose = (cwd: string | null): void => {
    onSelect(cwd);
    setOpen(false);
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          ref={trigger}
          variant="outline"
          size="sm"
          role="combobox"
          aria-label="Project context"
          aria-expanded={open}
          aria-controls={listId}
          data-testid="rail-project-context"
          // Match the action row while retaining the touch target.
          className="h-6 min-h-[24px] w-full justify-start gap-1.5 px-2 text-sm [@media(pointer:coarse)]:min-h-11"
        >
          <FolderGit2Icon className="size-3.5 shrink-0" aria-hidden />
          <span className="min-w-0 flex-1 truncate text-left">{label}</span>
          <ChevronsUpDownIcon className="size-3.5 shrink-0 text-content-tertiary" aria-hidden />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        className="w-72 p-0"
        onEscapeKeyDown={() => trigger.current?.focus()}
      >
        <Command>
          <CommandInput placeholder="Search projects" aria-label="Search projects" />
          <CommandList id={listId}>
            <CommandEmpty>No projects match that search.</CommandEmpty>
            <CommandGroup heading="Project context">
              <CommandItem
                value="All projects"
                onSelect={() => choose(null)}
                className="min-h-[28px] [@media(pointer:coarse)]:min-h-11"
              >
                <FolderGit2Icon aria-hidden />
                <span>All projects</span>
                {context.kind === "all" ? <CheckIcon className="ml-auto" aria-hidden /> : null}
              </CommandItem>
              {options.map(({ project, label: optionLabel }) => {
                const selected = context.kind === "selected" && context.cwd === project.cwd;
                return (
                  <CommandItem
                    key={project.cwd}
                    value={projectOptionValue(project, projects)}
                    onSelect={() => choose(project.cwd)}
                    className="min-h-[28px] [@media(pointer:coarse)]:min-h-11"
                  >
                    <FolderGit2Icon aria-hidden />
                    <span className="truncate">{optionLabel}</span>
                    {selected ? <CheckIcon className="ml-auto" aria-hidden /> : null}
                  </CommandItem>
                );
              })}
            </CommandGroup>
            {status === "loading" && projects.length === 0 ? (
              <p role="status" className="px-3 py-2 text-xs text-content-tertiary">
                Loading projects…
              </p>
            ) : null}
            {status === "error" ? (
              <p role="status" className="px-3 py-2 text-xs text-content-tertiary">
                Projects could not be refreshed. All projects remains available.
              </p>
            ) : null}
            {status === "ready" && projects.length === 0 ? (
              <p role="status" className="px-3 py-2 text-xs text-content-tertiary">
                No projects are configured.
              </p>
            ) : null}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
