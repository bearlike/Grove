"use client";

import { useState } from "react";
import { LoaderCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { defaultDeleteBranch } from "@/lib/grove/workspace-actions";
import type { WorkspaceStateView } from "@/lib/grove/types";

/**
 * The one destructive-confirm modal for `kill` — the single op that drops a
 * branch + worktree — carrying the delete-branch checkbox whose default follows
 * the engine's provenance rule (`defaultDeleteBranch`), matching the engine's
 * required-explicit-input stance. Extracted to its own file (#136) when the kill
 * TRIGGER moved from the `⋯` actions menu into the identity popover's danger
 * zone: the dialog is portalled + placement-agnostic, so it renders correctly
 * regardless of which control hosts the trigger, and lives once as its own
 * concern rather than nested inside either host.
 *
 * The kill→Dialog open flow (a control opening a Radix Dialog) is e2e-only: the
 * Radix focus-trap handoff blows the stack under jsdom, so no component test
 * drives it — the Playwright lifecycle spec exercises it in a real browser.
 *
 * Test seams: `kill-confirm-dialog`, `kill-delete-branch`, `kill-cancel`,
 * `kill-confirm` — unchanged by the move.
 */
export function KillConfirmDialog({
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
            className="min-h-11"
          >
            {pending && <LoaderCircle className="animate-spin" />}
            Kill workspace
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
