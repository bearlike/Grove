"use client";

import { useState } from "react";
import { LoaderCircle, Pause, Play, RotateCcw, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useWorkspaceActions } from "@/lib/grove/hooks";
import {
  availableActions,
  defaultDeleteBranch,
  type LifecycleAction,
} from "@/lib/grove/workspace-actions";
import { GroveProtocolError } from "@/lib/grove/client";
import type { WorkspaceStateView } from "@/lib/grove/types";

/**
 * Per-workspace lifecycle controls — the webapp's mirror of the TUI footer's
 * p/R/o/k verbs. Which buttons appear is the pure `availableActions(status,
 * placement)` gate (same matrix as the TUI); the engine is the real
 * precondition gate, so an illegal action just surfaces its typed refusal.
 *
 * pause/resume/respawn fire directly. Kill — the one destructive op that drops a
 * branch + worktree — is gated behind an explicit confirm carrying a
 * delete-branch checkbox whose default follows the engine's provenance rule
 * (`defaultDeleteBranch`), matching the engine's required-explicit-input stance.
 */
export function WorkspaceActions({
  state,
  onKilled,
}: {
  state: WorkspaceStateView;
  /** Called after a successful kill — the detail page navigates home. */
  onKilled?: () => void;
}) {
  const { pause, resume, respawn, kill } = useWorkspaceActions(state.id);
  const [confirmKill, setConfirmKill] = useState(false);

  const actions = availableActions(state.status, state.placement);
  const busy = pause.isPending || resume.isPending || respawn.isPending || kill.isPending;

  // Surface the most recent refusal from any verb as one inline message.
  const lastError = pause.error ?? resume.error ?? respawn.error ?? kill.error;
  const errorText =
    lastError instanceof GroveProtocolError
      ? lastError.message
      : lastError
        ? "Could not reach the daemon."
        : null;

  const has = (a: LifecycleAction) => actions.includes(a);

  return (
    <div className="flex flex-col items-end gap-1.5" data-testid="workspace-actions">
      <div className="flex flex-wrap items-center justify-end gap-1.5">
        {has("pause") && (
          <Button
            size="sm"
            variant="outline"
            data-testid="action-pause"
            disabled={busy}
            onClick={() => pause.mutate(false)}
          >
            {pause.isPending ? <LoaderCircle className="animate-spin" /> : <Pause />}
            Pause
          </Button>
        )}
        {has("resume") && (
          <Button
            size="sm"
            variant="outline"
            data-testid="action-resume"
            disabled={busy}
            onClick={() => resume.mutate()}
          >
            {resume.isPending ? <LoaderCircle className="animate-spin" /> : <Play />}
            Resume
          </Button>
        )}
        {has("respawn") && (
          <Button
            size="sm"
            variant="outline"
            data-testid="action-respawn"
            disabled={busy}
            onClick={() => respawn.mutate()}
          >
            {respawn.isPending ? <LoaderCircle className="animate-spin" /> : <RotateCcw />}
            Respawn
          </Button>
        )}
        {has("kill") && (
          <Button
            size="sm"
            variant="outline"
            data-testid="action-kill"
            disabled={busy}
            onClick={() => setConfirmKill(true)}
            className="border-[var(--status-error)]/40 text-[var(--status-error)] hover:bg-[var(--status-error)]/10 hover:text-[var(--status-error)]"
          >
            <Trash2 />
            Kill
          </Button>
        )}
      </div>

      {errorText && (
        <p
          role="alert"
          data-testid="action-error"
          className="max-w-xs text-right text-xs text-[var(--status-error)]"
        >
          {errorText}
        </p>
      )}

      <KillConfirmDialog
        open={confirmKill}
        onOpenChange={setConfirmKill}
        state={state}
        pending={kill.isPending}
        onConfirm={(deleteBranch) =>
          kill.mutate(deleteBranch, {
            onSuccess: () => {
              setConfirmKill(false);
              onKilled?.();
            },
          })
        }
      />
    </div>
  );
}

function KillConfirmDialog({
  open,
  onOpenChange,
  state,
  pending,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  state: WorkspaceStateView;
  pending: boolean;
  onConfirm: (deleteBranch: boolean) => void;
}) {
  const isRoot = state.placement === "root";
  // Default the checkbox from the engine's provenance rule; the user can flip it.
  const [deleteBranch, setDeleteBranch] = useState(() =>
    defaultDeleteBranch(state.branch_provenance, state.placement),
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="kill-confirm-dialog" className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Kill “{state.title}”?</DialogTitle>
          <DialogDescription>
            Tears down the tmux session
            {isRoot ? " (the repo-root branch stays untouched)." : " and removes the worktree."}{" "}
            This can&apos;t be undone.
          </DialogDescription>
        </DialogHeader>

        <label
          className="flex items-start gap-2 rounded-md border border-border bg-muted/30 p-3 text-sm"
          htmlFor="kill-delete-branch"
        >
          <input
            id="kill-delete-branch"
            data-testid="kill-delete-branch"
            type="checkbox"
            className="mt-0.5 size-4 rounded border-input accent-[var(--status-error)] disabled:opacity-50"
            checked={deleteBranch}
            disabled={isRoot}
            onChange={(e) => setDeleteBranch(e.target.checked)}
          />
          <span>
            <span className="font-medium">
              Also delete the local branch{" "}
              <span className="font-mono text-[var(--ref-branch)]">{state.branch}</span>
            </span>
            <span className="mt-0.5 block text-xs text-muted-foreground">
              {isRoot
                ? "Root workspaces never delete the branch — it's the one you had checked out."
                : "Remote branches are never touched by Grove."}
            </span>
          </span>
        </label>

        <DialogFooter>
          <Button variant="outline" data-testid="kill-cancel" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            data-testid="kill-confirm"
            disabled={pending}
            onClick={() => onConfirm(isRoot ? false : deleteBranch)}
          >
            {pending && <LoaderCircle className="animate-spin" />}
            Kill workspace
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
