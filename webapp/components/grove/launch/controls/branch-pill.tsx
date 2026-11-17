"use client";

import { useMemo, type ReactNode } from "react";
import {
  GitBranchIcon,
  GitCommitHorizontalIcon,
  GitForkIcon,
  type LucideIcon,
} from "lucide-react";

import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { LaunchPill, type LaunchPillOption } from "../control-pill";
import { type BranchMode, useLaunchControls, useLaunchProblem } from "../launch-state";
import { currentPreview } from "@/lib/grove/adapters/launch";
import { runtimeKindOf } from "@/lib/grove/adapters/launch-flow";
import { useBranches } from "@/lib/grove/hooks";

const BRANCH_NAME_PATTERN = /^[A-Za-z0-9._/][A-Za-z0-9._/\-]*$/;

type BranchModeDefinition = {
  readonly label: string;
  readonly glyph: LucideIcon;
  readonly description?: string;
  /**
   * Choosing this mode reveals fields, so the menu must stay open.
   *
   * `auto` is the only complete answer in the table — every other mode needs a
   * name, a ref or a decision typed underneath it, and those fields live in the
   * popover the selection would otherwise dismiss.
   */
  readonly opensPanel?: true;
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
 * Every answer is one vocabulary, and every glyph in it is git-family.
 *
 * That is the rule, not an aesthetic: this row sits beside a project control and
 * a working-directory control, both of which are legitimately about folders, so
 * a folder mark HERE reads as one of them rather than as a placement choice.
 *
 * `root` leads because it is the built-in answer: work in the checkout you
 * already have. The rest are the ways to get an isolated worktree, grouped
 * under their own heading so the two kinds of answer never read as one list.
 */
const PLACEMENTS: Readonly<Record<BranchMode, BranchModeDefinition & { readonly group: string }>> = {
  root: {
    label: "Current checkout",
    glyph: GitCommitHorizontalIcon,
    description: "no new branch or worktree",
    group: "Work in place",
  },
  // "Auto" named the mechanism, not the outcome — it told you Grove would
  // decide without telling you what it would decide. Every other option here
  // says what you get, so this one does too.
  auto: {
    label: "New branch",
    glyph: GitBranchIcon,
    description: "named after your task",
    group: "Isolated worktree",
  },
  new: {
    label: "New branch…",
    glyph: GitBranchIcon,
    description: "you choose the name",
    opensPanel: true,
    group: "Isolated worktree",
  },
  existing: {
    label: "Existing local branch",
    glyph: GitBranchIcon,
    opensPanel: true,
    group: "Isolated worktree",
  },
  remote: {
    label: "Track a remote branch",
    glyph: GitForkIcon,
    opensPanel: true,
    group: "Isolated worktree",
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

/**
 * Where the work lands inside the project: the current checkout, or an
 * isolated worktree on a branch this pill also chooses.
 *
 * It owns the init-script choice too, because that question only exists for
 * the current checkout: the script is written for a fresh worktree. The
 * Project menu used to carry both, which buried its own list under two forms.
 */
export function BranchPill(): ReactNode {
  const state = useLaunchControls();
  const { values, set } = state;
  const preview = currentPreview(state);
  const problem = useLaunchProblem("branchMode");
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
  const remote =
    preview?.runtime === "remote" ||
    (values.runtime !== null && runtimeKindOf(values.runtime) === "remote");
  const adjustedPlacement = preview?.adjustments.some((adjustment) => adjustment.field === "branch_plan") === true;
  const effectiveBranchMode: BranchMode =
    adjustedPlacement && !state.touched.has("branchMode") && preview?.branch_plan?.kind === "auto"
      ? "auto"
      : values.branchMode;
  const options = useMemo<readonly LaunchPillOption[]>(
    () =>
      (
        Object.entries(PLACEMENTS) as readonly [
          BranchMode,
          (typeof PLACEMENTS)[BranchMode],
        ][]
      ).map(([id, definition]) => {
        const Glyph = definition.glyph;
        return {
          id,
          label: definition.label,
          group: definition.group,
          icon: <Glyph aria-hidden />,
          opensPanel: definition.opensPanel,
          ...(definition.description === undefined
            ? {}
            : { description: definition.description }),
          ...(id === "root" && remote
            ? { disabled: true, description: "Remote runs need branches" }
            : id === "existing" && existingReason !== undefined
              ? { disabled: true, description: existingReason }
              : {}),
        };
      }),
    [existingReason, remote],
  );
  const ActiveGlyph = PLACEMENTS[effectiveBranchMode].glyph;
  const newNameError =
    effectiveBranchMode === "new" ? branchNameError(values.branchName) : null;
  const localNameError =
    effectiveBranchMode === "remote" && values.localName !== ""
      ? branchNameError(values.localName)
      : null;

  return (
    <LaunchPill
      kind="branch"
      ariaLabel="Placement"
      value={effectiveBranchMode}
      options={options}
      onSelect={(branchMode) => set({ branchMode: branchMode as BranchMode })}
      searchNoun="placements"
      leading={<ActiveGlyph aria-hidden />}
      disabledReason={values.repoRoot === null ? "Choose a project first" : undefined}
      // The name is typed inside the popover, so its error goes with it when
      // the menu closes — and Send is disabled for a reason the user can no
      // longer see. The trigger keeps the sentence.
      error={newNameError ?? localNameError ?? problem}
    >
      {effectiveBranchMode === "root" ? (
        // The decision has to be DISCLOSED where it is made: setting skip-init
        // silently would be Grove making a choice it never told anyone about,
        // and the script is written for a fresh worktree, so running it in the
        // live checkout can be destructive.
        <BranchField
          label="Init script"
          htmlFor="launch-root-init"
          help="It is written for a fresh worktree, so it may not be safe in the checkout you are already using."
        >
          <Select
            value={values.skipInit ? "skip" : "run"}
            onValueChange={(choice) => set({ skipInit: choice === "skip" })}
          >
            <SelectTrigger id="launch-root-init" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="skip">Skip it (recommended here)</SelectItem>
              <SelectItem value="run">Run it anyway</SelectItem>
            </SelectContent>
          </Select>
        </BranchField>
      ) : null}
      {effectiveBranchMode === "new" ? (
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
      {effectiveBranchMode === "existing" ? (
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
      {effectiveBranchMode === "remote" ? (
        // No wrapper of its own: `LaunchPill` supplies the panel's frame and
        // stacking for every pill, so a second one here was the extra padding
        // that made this the one mode whose fields sat lower than the rest.
        <>
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
        </>
      ) : null}
    </LaunchPill>
  );
}
