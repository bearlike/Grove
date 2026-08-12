"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import type { WorkspaceStateView } from "@/lib/grove/api";
import { defaultDeleteBranch } from "./selectors";

/**
 * Kill destroys the worktree and, usually, the branch — so the branch decision
 * is an explicit input rather than something inferred at the last moment. The
 * checkbox opens on what `delete_branch=null` would have resolved to
 * engine-side, and a root-placement workspace cannot delete its branch at all.
 */
export function KillDialog({
  state,
  open,
  onOpenChange,
  onConfirm,
  pending,
}: {
  state: WorkspaceStateView;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: (deleteBranch: boolean) => void;
  pending: boolean;
}) {
  const isRoot = state.placement === "root";
  const [deleteBranch, setDeleteBranch] = useState(() => defaultDeleteBranch(state));

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="kill-dialog">
        <DialogHeader>
          <DialogTitle>Kill {state.title}?</DialogTitle>
          <DialogDescription>
            This removes the worktree and the tmux session. Remote branches are never touched
            by Grove.
          </DialogDescription>
        </DialogHeader>
        <div className="flex items-center gap-2">
          <input
            id="kill-delete-branch"
            type="checkbox"
            checked={deleteBranch && !isRoot}
            disabled={isRoot}
            onChange={(event) => setDeleteBranch(event.target.checked)}
            data-testid="kill-delete-branch"
          />
          <Label htmlFor="kill-delete-branch">
            Delete the local branch <span className="font-mono">{state.branch}</span>
          </Label>
        </div>
        {isRoot && <p className="text-xs">A root-placement workspace keeps its branch.</p>}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            disabled={pending}
            onClick={() => onConfirm(deleteBranch && !isRoot)}
            data-testid="kill-confirm"
          >
            Kill workspace
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
