"use client";

import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type {
  UpdateWorkspaceRequest,
  WorkspaceStateView,
} from "@/lib/grove/api";
import { useUpdateWorkspace } from "@/lib/grove/hooks";

export type WorkspaceEditField = "title" | "description";

/** The daemon rejects an empty title; keep that refusal beside the editable field. */
export function workspaceTitleError(title: string): string | null {
  return title.trim() === "" ? "A workspace title is required." : null;
}

/** Preserve an empty description: it is the wire-level instruction to clear it. */
export function workspaceUpdateRequest(
  title: string,
  description: string,
): UpdateWorkspaceRequest | null {
  const trimmedTitle = title.trim();
  if (workspaceTitleError(trimmedTitle)) return null;
  return { title: trimmedTitle, description };
}

/** Edit the two mutable pieces of workspace metadata from the fleet rail. */
export function RenameWorkspaceDialog({
  state,
  open,
  onOpenChange,
  initialFocus,
}: {
  state: WorkspaceStateView;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initialFocus: WorkspaceEditField;
}): React.ReactNode {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {open ? (
        <RenameWorkspaceForm
          key={`${state.id}:${initialFocus}`}
          state={state}
          initialFocus={initialFocus}
          onSaved={() => onOpenChange(false)}
        />
      ) : null}
    </Dialog>
  );
}

function RenameWorkspaceForm({
  state,
  initialFocus,
  onSaved,
}: {
  state: WorkspaceStateView;
  initialFocus: WorkspaceEditField;
  onSaved: () => void;
}): React.ReactNode {
  const titleId = useId();
  const descriptionId = useId();
  const titleErrorId = useId();
  const [title, setTitle] = useState(state.title);
  const [description, setDescription] = useState(state.description ?? "");
  const update = useUpdateWorkspace(state.id);
  const titleError = workspaceTitleError(title);

  const submit = (event: React.FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const request = workspaceUpdateRequest(title, description);
    if (!request) return;
    update.mutate(request, { onSuccess: onSaved });
  };

  return (
    <DialogContent data-testid="rename-workspace-dialog">
      <DialogHeader>
        <DialogTitle>Edit workspace</DialogTitle>
        <DialogDescription>
          Rename this workspace or describe what it is for.
        </DialogDescription>
      </DialogHeader>
      <form className="flex flex-col gap-4" onSubmit={submit}>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={titleId}>Title</Label>
          <Input
            id={titleId}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            aria-invalid={titleError ? true : undefined}
            aria-describedby={titleError ? titleErrorId : undefined}
            autoFocus={initialFocus === "title"}
            data-testid="rename-workspace-title"
          />
          {titleError ? (
            <p id={titleErrorId} role="alert" className="text-sm">
              {titleError}
            </p>
          ) : null}
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={descriptionId}>Description</Label>
          <Input
            id={descriptionId}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder="Optional"
            autoFocus={initialFocus === "description"}
            data-testid="rename-workspace-description"
          />
        </div>
        {update.error ? (
          <p role="alert">Could not save workspace: {update.error.message}</p>
        ) : null}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onSaved()}>
            Cancel
          </Button>
          <Button
            type="submit"
            disabled={update.isPending}
            data-testid="rename-workspace-save"
          >
            Save
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
