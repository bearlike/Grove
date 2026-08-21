"use client";

import { useMemo, type ReactNode } from "react";
import {
  FolderRootIcon,
  GitBranchIcon,
  GitForkIcon,
  type LucideIcon,
} from "lucide-react";

import {
  ModelSelectorList,
  ModelSelectorSearch,
} from "@/components/assistant-ui/model-selector";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { LaunchPill, type LaunchPillOption } from "../control-pill";
import { type BranchMode, useLaunchControls } from "../launch-state";
import { useBranches } from "@/lib/grove/hooks";

const BRANCH_NAME_PATTERN = /^[A-Za-z0-9._/][A-Za-z0-9._/\-]*$/;

type BranchModeDefinition = {
  readonly label: string;
  readonly glyph: LucideIcon;
  readonly description?: string;
};

type BranchFieldProps = {
  readonly label: string;
  readonly htmlFor: string;
  readonly children: ReactNode;
  readonly help?: string;
  readonly error?: string | null;
};

/**
 * A branch panel field keeps labels and supplemental copy quieter than the
 * selected ref, while preserving destructive text as an error signal.
 */
function BranchField({
  label,
  htmlFor,
  children,
  help,
  error,
}: BranchFieldProps): ReactNode {
  return (
    <div className="flex flex-col gap-2">
      <label htmlFor={htmlFor} className="text-xs text-content-tertiary">
        {label}
      </label>
      {children}
      {help ? <p className="text-xs text-content-tertiary">{help}</p> : null}
      {error ? <p className="text-xs text-destructive">{error}</p> : null}
    </div>
  );
}

/**
 * The five answers are one vocabulary. Root deliberately wears a placement mark:
 * it changes where work runs rather than choosing a git ref.
 */
const BRANCH_MODES: Readonly<Record<BranchMode, BranchModeDefinition>> = {
  // "Auto" named the mechanism, not the outcome — it told you Grove would
  // decide without telling you what it would decide. Every other option here
  // says what you get, so this one does too.
  auto: {
    label: "New branch",
    glyph: GitBranchIcon,
    description: "named after your task",
  },
  new: { label: "New branch…", glyph: GitBranchIcon, description: "you choose the name" },
  existing: { label: "Existing local branch", glyph: GitBranchIcon },
  remote: { label: "Track a remote branch", glyph: GitForkIcon },
  root: {
    label: "Repo root",
    glyph: FolderRootIcon,
    description: "no worktree · no isolation · no pause/resume",
  },
};

function branchNameError(name: string): string | null {
  if (name === "") return "Enter a branch name.";
  return BRANCH_NAME_PATTERN.test(name)
    ? null
    : "Use letters, numbers, dots, slashes, underscores, or dashes; do not start with a dash.";
}

function localAvailabilityReason(
  pending: boolean,
  failed: boolean,
  available: number,
): string | undefined {
  if (pending) return "Loading local branches…";
  if (failed) return "Could not read local branches.";
  if (available === 0) return "No local branches are available to check out.";
  return undefined;
}

/** The branch source and placement control in the launch row. */
export function BranchPill(): ReactNode {
  const { values, set } = useLaunchControls();
  const localBranches = useBranches(values.repoRoot, "local");
  const remoteBranches = useBranches(values.repoRoot, "remote");
  const availableLocalBranches = useMemo(
    () =>
      (localBranches.data ?? []).filter(
        (branch) =>
          branch.checked_out_in === null || branch.checked_out_in === undefined,
      ),
    [localBranches.data],
  );
  const existingReason = localAvailabilityReason(
    localBranches.isPending,
    localBranches.isError,
    availableLocalBranches.length,
  );
  const options = useMemo<readonly LaunchPillOption[]>(
    () =>
      (
        Object.entries(BRANCH_MODES) as readonly [
          BranchMode,
          BranchModeDefinition,
        ][]
      ).map(([id, definition]) => {
        const Glyph = definition.glyph;
        return {
          id,
          label: definition.label,
          icon: <Glyph aria-hidden />,
          ...(definition.description === undefined
            ? {}
            : { description: definition.description }),
          ...(id === "existing" && existingReason !== undefined
            ? { disabled: true, description: existingReason }
            : {}),
        };
      }),
    [existingReason],
  );
  const ActiveGlyph = BRANCH_MODES[values.branchMode].glyph;
  const newNameError =
    values.branchMode === "new" ? branchNameError(values.branchName) : null;
  const localNameError =
    values.branchMode === "remote" && values.localName !== ""
      ? branchNameError(values.localName)
      : null;

  return (
    <LaunchPill
      kind="branch"
      ariaLabel="Branch and placement"
      value={values.branchMode}
      options={options}
      onSelect={(branchMode) => set({ branchMode: branchMode as BranchMode })}
      searchable
      leading={<ActiveGlyph aria-hidden />}
      disabledReason={values.repoRoot === null ? "Choose a project first" : undefined}
    >
      <ModelSelectorSearch placeholder="Search branch choices…" />
      <ModelSelectorList />
      {values.branchMode === "root" ? (
        // The overflow control used to carry this checkbox. With the overflow
        // gone the decision still has to be DISCLOSED where it is made —
        // setting skip-init silently would be Grove making a choice it never
        // told anyone about, and the init script is written for a fresh
        // worktree, so running it in the live root can be destructive.
        <BranchField
          label="Init script"
          htmlFor="launch-root-init"
          help="The init script is written for a fresh worktree, so it may not be safe to run in the checkout you are already using."
        >
          <Select
            value={values.skipInit ? "skip" : "run"}
            onValueChange={(choice) => set({ skipInit: choice === "skip" })}
          >
            <SelectTrigger id="launch-root-init" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="skip">Skip it (recommended for the root)</SelectItem>
              <SelectItem value="run">Run it anyway</SelectItem>
            </SelectContent>
          </Select>
        </BranchField>
      ) : null}
      {values.branchMode === "new" ? (
        <BranchField
          label="Branch name"
          htmlFor="launch-new-branch"
          error={newNameError}
        >
          <Input
            id="launch-new-branch"
            value={values.branchName}
            onChange={(event) => set({ branchName: event.target.value })}
            placeholder="feature/payment-v2"
            aria-invalid={newNameError !== null}
          />
        </BranchField>
      ) : null}
      {values.branchMode === "existing" ? (
        <BranchField label="Local branch" htmlFor="launch-existing-branch">
          <Select
            value={values.existingBranch}
            onValueChange={(existingBranch) => set({ existingBranch })}
          >
            <SelectTrigger id="launch-existing-branch" className="w-full">
              <SelectValue placeholder="Pick a local branch" />
            </SelectTrigger>
            <SelectContent>
              {(localBranches.data ?? []).map((branch) => {
                const checkedOutIn = branch.checked_out_in ?? null;
                return (
                  <SelectItem
                    key={branch.name}
                    value={branch.name}
                    disabled={checkedOutIn !== null}
                  >
                    {branch.name}
                    {checkedOutIn ? ` (checked out in ${checkedOutIn})` : ""}
                  </SelectItem>
                );
              })}
            </SelectContent>
          </Select>
        </BranchField>
      ) : null}
      {values.branchMode === "remote" ? (
        <div className="flex flex-col gap-2 p-3">
          <BranchField
            label="Remote branch"
            htmlFor="launch-remote-branch"
            help={
              (remoteBranches.data ?? []).length === 0 && !remoteBranches.isPending
                ? "No remote branches found — fetch first?"
                : undefined
            }
          >
            <Select
              value={values.remoteRef}
              onValueChange={(remoteRef) => set({ remoteRef })}
            >
              <SelectTrigger id="launch-remote-branch" className="w-full">
                <SelectValue placeholder="Pick a remote branch" />
              </SelectTrigger>
              <SelectContent>
                {(remoteBranches.data ?? []).map((branch) => (
                  <SelectItem key={branch.name} value={branch.name}>
                    {branch.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </BranchField>
          <BranchField
            label="Local name (optional)"
            htmlFor="launch-local-branch"
            error={localNameError}
          >
            <Input
              id="launch-local-branch"
              value={values.localName}
              onChange={(event) => set({ localName: event.target.value })}
              placeholder="Derived from remote ref"
              aria-invalid={localNameError !== null}
            />
          </BranchField>
        </div>
      ) : null}
    </LaunchPill>
  );
}
