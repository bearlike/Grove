"use client";

import { useState } from "react";
import { CheckSquareIcon, FlipHorizontal2Icon, PauseIcon, PlayIcon, SquareIcon, Trash2Icon } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useBulkWorkspaceActions, type BulkOutcome, type BulkVerb } from "@/lib/grove/hooks";
import { bulkTargets, invertSelection, NO_SELECTION, selectAll, workingCount, type Selection } from "./kanban";
import type { FleetRow } from "./types";

/**
 * Select all · invert · clear, then the verbs the selection can take.
 *
 * "All" and "invert" are over the VISIBLE rows, which is what makes the
 * filter a bulk instrument: narrow to a project and a state, select all, act.
 * The verbs come from `bulkTargets`, so a mixed selection offers pause for
 * its live rows and resume for its paused ones at once, each applied only
 * where it can land.
 */
export function FleetBulkBar({
  visible,
  selected,
  onSelectionChange,
}: {
  visible: readonly FleetRow[];
  selected: Selection;
  onSelectionChange: (selection: Selection) => void;
}): React.ReactNode {
  const targets = bulkTargets(visible, selected);
  const bulk = useBulkWorkspaceActions();
  const [confirmKill, setConfirmKill] = useState(false);
  const allSelected = visible.length > 0 && selected.size === visible.length;

  const run = (verb: BulkVerb, ids: readonly string[]): void => {
    bulk.mutate(
      { verb, ids },
      {
        onSuccess: (outcome) => {
          report(outcome);
          // A killed workspace is gone from every list; a paused or resumed
          // one stays selected so a follow-up verb finds it.
          if (verb === "kill") onSelectionChange(NO_SELECTION);
        },
      },
    );
  };

  return (
    <div className="flex flex-wrap items-center gap-1.5" data-testid="fleet-bulk-bar" data-selected-count={selected.size}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 gap-1.5 px-2"
            onClick={() => onSelectionChange(allSelected ? NO_SELECTION : selectAll(visible))}
            disabled={visible.length === 0}
            aria-pressed={allSelected}
            data-testid="fleet-select-all"
          >
            {allSelected ? <CheckSquareIcon className="size-3.5" aria-hidden /> : <SquareIcon className="size-3.5" aria-hidden />}
            <span className="text-xs">{allSelected ? "Clear" : "Select all"}</span>
          </Button>
        </TooltipTrigger>
        <TooltipContent>{allSelected ? "Clear the selection" : `Select the ${visible.length} visible workspaces`}</TooltipContent>
      </Tooltip>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 gap-1.5 px-2"
            onClick={() => onSelectionChange(invertSelection(visible, selected))}
            disabled={visible.length === 0}
            data-testid="fleet-select-invert"
          >
            <FlipHorizontal2Icon className="size-3.5" aria-hidden />
            <span className="text-xs">Invert</span>
          </Button>
        </TooltipTrigger>
        <TooltipContent>Select the visible workspaces that are not selected, and drop the ones that are</TooltipContent>
      </Tooltip>

      {selected.size > 0 ? (
        <>
          <span className="px-1 text-xs tabular-nums text-content-tertiary" data-testid="fleet-selected-count">
            {selected.size} selected
          </span>
          <Verb
            label="Pause"
            icon={<PauseIcon className="size-3.5" aria-hidden />}
            count={targets.pause.length}
            pending={bulk.isPending}
            onClick={() => run("pause", targets.pause)}
            testId="fleet-bulk-pause"
          />
          <Verb
            label="Resume"
            icon={<PlayIcon className="size-3.5" aria-hidden />}
            count={targets.resume.length}
            pending={bulk.isPending}
            onClick={() => run("resume", targets.resume)}
            testId="fleet-bulk-resume"
          />
          <Verb
            label="Kill"
            icon={<Trash2Icon className="size-3.5" aria-hidden />}
            count={targets.kill.length}
            pending={bulk.isPending}
            destructive
            onClick={() => setConfirmKill(true)}
            testId="fleet-bulk-kill"
          />
          <Dialog open={confirmKill} onOpenChange={setConfirmKill}>
            <DialogContent data-testid="fleet-bulk-kill-dialog">
              <DialogHeader>
                <DialogTitle>Kill {targets.kill.length} workspaces?</DialogTitle>
                <DialogDescription>
                  This removes each worktree and tmux session. Each branch is kept or deleted the
                  way its own workspace would decide; remote branches are never touched.
                  {workingCount(visible, selected) > 0
                    ? ` ${workingCount(visible, selected)} of them are still working.`
                    : ""}
                </DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <Button variant="outline" onClick={() => setConfirmKill(false)}>
                  Cancel
                </Button>
                <Button
                  variant="destructive"
                  disabled={bulk.isPending}
                  onClick={() => {
                    setConfirmKill(false);
                    run("kill", targets.kill);
                  }}
                  data-testid="fleet-bulk-kill-confirm"
                >
                  Kill {targets.kill.length} workspaces
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </>
      ) : null}
    </div>
  );
}

/** A verb is offered only when some selected row can take it; the count says how many. */
function Verb({
  label,
  icon,
  count,
  pending,
  destructive = false,
  onClick,
  testId,
}: {
  label: string;
  icon: React.ReactNode;
  count: number;
  pending: boolean;
  destructive?: boolean;
  onClick: () => void;
  testId: string;
}): React.ReactNode {
  if (count === 0) return null;
  return (
    <Button
      variant={destructive ? "destructive" : "outline"}
      size="sm"
      className="h-7 gap-1.5 px-2"
      disabled={pending}
      onClick={onClick}
      data-testid={testId}
    >
      {icon}
      <span className="text-xs">
        {label} <span className="tabular-nums">{count}</span>
      </span>
    </Button>
  );
}

/** One toast per batch, stating both halves of a partial outcome. */
function report(outcome: BulkOutcome): void {
  const word = { pause: "Paused", resume: "Resumed", kill: "Killed" }[outcome.verb];
  if (outcome.failed.length === 0) {
    toast.success(`${word} ${outcome.succeeded.length} workspaces`);
    return;
  }
  toast.error(`${word} ${outcome.succeeded.length}, ${outcome.failed.length} failed`, {
    description: outcome.failed.map(({ id, error }) => `${id.slice(0, 10)}: ${error.message}`).join("\n"),
  });
}
