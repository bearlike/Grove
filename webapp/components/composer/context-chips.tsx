"use client";

import { ChevronDown, ChevronRight } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { MetaRow } from "@/components/shared/meta";
import { cn } from "@/lib/utils";
import type { BranchInfo, BranchPlan } from "@/lib/grove/types";
import type { BranchMode, ComposerDraft } from "@/lib/grove/ui-store";
import { PillTrigger } from "./pill";

/** Short, scannable label per branch mode for the chip + the mode menu. */
const MODE_SHORT: Record<BranchMode, string> = {
  auto: "Auto branch",
  new_named: "New branch",
  existing_local: "Existing branch",
  track_remote: "Track remote",
  root: "Repo root",
};
const MODE_HELP: Record<BranchMode, string> = {
  auto: "Grove generates a branch from the title + timestamp",
  new_named: "Pick a name and base branch",
  existing_local: "Check out a local branch",
  track_remote: "Track a remote branch",
  root: "Work in the repo root (no worktree)",
};
const MODES = Object.keys(MODE_SHORT) as BranchMode[];

export interface RepoOption {
  root: string;
  name: string;
}

/**
 * The borderless inline chip row BELOW the composer card: repo · base branch ·
 * branch mode, plus an `Advanced ▾` text disclosure that reveals branch source
 * (the 5 modes + their per-mode fields) and the skip-init checkbox. Collapsed by
 * default so the common create — type a prompt, hit Enter — never opens it.
 *
 * Presentational: the container owns the composer slice + the branch lists and
 * passes a narrow `draft`/`patch` pair; the per-mode field testids mirror the old
 * create dialog so existing test/muscle-memory carries over.
 */
export function ContextChips({
  draft,
  patch,
  repos,
  localBranches,
  remoteBranches,
}: {
  draft: ComposerDraft;
  patch: (p: Partial<ComposerDraft>) => void;
  repos: RepoOption[];
  localBranches: BranchInfo[];
  remoteBranches: BranchInfo[];
}) {
  const repo = repos.find((r) => r.root === draft.repoRoot) ?? null;
  const baseOptions = ["HEAD", ...localBranches.map((b) => b.name).filter((n) => n !== "HEAD")];
  // Base ref only reads as meaningful for the modes that consume it.
  const baseShown = draft.branchMode === "auto" || draft.branchMode === "new_named";

  function setMode(next: BranchMode) {
    // Mirror the TUI: choosing Root auto-checks skip-init (init scripts target a
    // fresh worktree, risky in the repo root). One-way nudge — back never clears it.
    patch(next === "root" ? { branchMode: next, skipInit: true } : { branchMode: next });
  }

  return (
    <div className="mt-2 flex flex-col gap-2">
      <MetaRow className="text-xs">
        {/* Repo selector — only a menu when more than one project exists. */}
        <RepoChip repos={repos} value={draft.repoRoot} onChange={(r) => patch({ repoRoot: r })} />
        {baseShown ? (
          <span data-testid="composer-base-chip" className="font-mono text-[var(--ref-branch)]">
            {draft.baseRef}
          </span>
        ) : null}
        <span data-testid="composer-mode-chip">{MODE_SHORT[draft.branchMode]}</span>
        <button
          type="button"
          data-testid="composer-advanced-toggle"
          aria-expanded={draft.advancedOpen}
          onClick={() => patch({ advancedOpen: !draft.advancedOpen })}
          className="inline-flex items-center gap-0.5 text-xs font-medium text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
        >
          {draft.advancedOpen ? (
            <ChevronDown className="size-3.5" aria-hidden />
          ) : (
            <ChevronRight className="size-3.5" aria-hidden />
          )}
          Advanced
        </button>
      </MetaRow>

      {draft.advancedOpen ? (
        <div
          data-testid="composer-advanced"
          className="grid gap-3 rounded-lg border border-border bg-muted/40 p-3"
        >
          <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
            Branch &amp; init
          </p>
          <Field label="Branch source" htmlFor="create-mode">
            <NativeSelect
              id="create-mode"
              data-testid="create-mode"
              className="h-9 text-sm"
              value={draft.branchMode}
              onChange={(e) => setMode(e.target.value as BranchMode)}
            >
              {MODES.map((m) => (
                <option key={m} value={m}>
                  {MODE_SHORT[m]} — {MODE_HELP[m]}
                </option>
              ))}
            </NativeSelect>
          </Field>

          {(draft.branchMode === "auto" || draft.branchMode === "new_named") && (
            <ModeFields>
              {draft.branchMode === "new_named" && (
                <Field label="Branch name" htmlFor="create-new-name">
                  <Input
                    id="create-new-name"
                    data-testid="create-new-name"
                    className="h-9 text-sm"
                    placeholder="feature/payment-v2"
                    value={draft.newName}
                    onChange={(e) => patch({ newName: e.target.value })}
                  />
                </Field>
              )}
              <Field label="Base ref" htmlFor="create-base">
                <NativeSelect
                  id="create-base"
                  data-testid="create-base"
                  className="h-9 text-sm"
                  value={draft.baseRef}
                  onChange={(e) => patch({ baseRef: e.target.value })}
                >
                  {baseOptions.map((b) => (
                    <option key={b} value={b}>
                      {b}
                    </option>
                  ))}
                </NativeSelect>
              </Field>
            </ModeFields>
          )}

          {draft.branchMode === "existing_local" && (
            <ModeFields>
              <Field label="Local branch" htmlFor="create-existing">
                <NativeSelect
                  id="create-existing"
                  data-testid="create-existing"
                  className="h-9 text-sm"
                  value={draft.existingName}
                  onChange={(e) => patch({ existingName: e.target.value })}
                >
                  <option value="" disabled>
                    {localBranches.length > 0 ? "Pick a branch…" : "No local branches"}
                  </option>
                  {localBranches.map((b) => (
                    <option key={b.name} value={b.name}>
                      {b.name}
                      {b.is_current ? " (current)" : ""}
                      {b.checked_out_in ? " (checked out)" : ""}
                    </option>
                  ))}
                </NativeSelect>
              </Field>
            </ModeFields>
          )}

          {draft.branchMode === "track_remote" && (
            <ModeFields>
              <Field label="Remote branch" htmlFor="create-remote-ref">
                <NativeSelect
                  id="create-remote-ref"
                  data-testid="create-remote-ref"
                  className="h-9 text-sm"
                  value={draft.remoteRef}
                  onChange={(e) => patch({ remoteRef: e.target.value })}
                >
                  <option value="" disabled>
                    {remoteBranches.length > 0
                      ? "Pick a remote branch…"
                      : "No remote branches (git fetch first?)"}
                  </option>
                  {remoteBranches.map((b) => (
                    <option key={b.name} value={b.name}>
                      {b.name}
                    </option>
                  ))}
                </NativeSelect>
              </Field>
              <Field label="Local name (optional)" htmlFor="create-remote-local">
                <Input
                  id="create-remote-local"
                  data-testid="create-remote-local"
                  className="h-9 text-sm"
                  placeholder="<derived from remote>"
                  value={draft.remoteLocal}
                  onChange={(e) => patch({ remoteLocal: e.target.value })}
                />
              </Field>
            </ModeFields>
          )}

          {draft.branchMode === "root" && (
            <p
              className="rounded-md border border-border bg-muted/40 p-3 text-xs text-muted-foreground"
              data-testid="create-root-note"
            >
              Runs in the repo root on the current branch. No worktree is created; pause and
              resume do not apply (use kill to stop, respawn to restart).
            </p>
          )}

          <label className="flex items-center gap-2 text-sm" htmlFor="create-skip-init">
            <input
              id="create-skip-init"
              data-testid="create-skip-init"
              type="checkbox"
              className="size-4 rounded border-input accent-primary"
              checked={draft.skipInit}
              onChange={(e) => patch({ skipInit: e.target.checked })}
            />
            Skip init script
          </label>

          <Field label="Resume session id (optional)" htmlFor="create-resume-session-id">
            <Input
              id="create-resume-session-id"
              data-testid="create-resume-session-id"
              className="h-9 font-mono text-sm"
              placeholder="Adopt an existing agent session instead of starting fresh"
              value={draft.resumeSessionId}
              onChange={(e) => patch({ resumeSessionId: e.target.value })}
            />
          </Field>
        </div>
      ) : null}
    </div>
  );
}

/**
 * The repo chip — a DropdownMenu of known projects (from the `/activity`
 * snapshot, so empty/config-declared repos are creatable). Renders a plain
 * non-interactive chip when only one project exists (nothing to choose).
 */
function RepoChip({
  repos,
  value,
  onChange,
}: {
  repos: RepoOption[];
  value: string | null;
  onChange: (root: string) => void;
}) {
  const current = repos.find((r) => r.root === value) ?? null;
  const label = current?.name ?? "No project";

  if (repos.length <= 1) {
    return (
      <span data-testid="composer-repo" data-repo={current?.root} title={current?.root}>
        {label}
      </span>
    );
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <PillTrigger
          data-testid="composer-repo"
          data-repo={current?.root}
          aria-label={`Project: ${label}`}
          className="h-6"
        >
          <span className="truncate">{label}</span>
          <ChevronDown className="size-3.5 opacity-60" aria-hidden />
        </PillTrigger>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="max-h-72 overflow-y-auto">
        <DropdownMenuLabel>Project</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {repos.map((r) => (
          <DropdownMenuItem
            key={r.root}
            data-repo={r.root}
            title={r.root}
            onSelect={() => onChange(r.root)}
          >
            <span className="truncate">{r.name}</span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function Field({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid gap-1">
      <Label htmlFor={htmlFor} className="text-xs font-medium text-muted-foreground">
        {label}
      </Label>
      {children}
    </div>
  );
}

function ModeFields({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={cn("grid gap-3", className)}>{children}</div>;
}

/** Build the wire `BranchPlan` from the composer draft (the 5-variant union). */
export function buildBranchPlan(draft: ComposerDraft): BranchPlan {
  switch (draft.branchMode) {
    case "new_named":
      return { kind: "new_named", name: draft.newName.trim(), base_ref: draft.baseRef };
    case "existing_local":
      return { kind: "existing_local", name: draft.existingName };
    case "track_remote":
      return {
        kind: "track_remote",
        remote_ref: draft.remoteRef,
        local_name: draft.remoteLocal.trim() || null,
      };
    case "root":
      return { kind: "root" };
    case "auto":
    default:
      return { kind: "auto", base_ref: draft.baseRef };
  }
}

/** Client-side completeness only — the engine owns real validation; this just
 *  avoids a guaranteed 422 when a mode's required field is still empty. */
export function branchPlanReady(draft: ComposerDraft): boolean {
  switch (draft.branchMode) {
    case "new_named":
      return draft.newName.trim().length > 0;
    case "existing_local":
      return draft.existingName.length > 0;
    case "track_remote":
      return draft.remoteRef.length > 0;
    case "auto":
    case "root":
      return true;
  }
}
