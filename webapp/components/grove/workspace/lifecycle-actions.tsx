"use client";

import { useState } from "react";
import { PauseIcon, PlayIcon, RotateCwIcon, Trash2Icon } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { WorkspaceStateView } from "@/lib/grove/api";
import { useWorkspaceActions } from "@/lib/grove/hooks";
import { KillDialog } from "./kill-dialog";
import { availableActions, type LifecycleAction } from "./selectors";

/**
 * Pause · resume · respawn · kill.
 *
 * The buttons offered mirror the engine's own gate so the surface reads
 * honestly, but the engine remains the real one: a stale snapshot offering an
 * illegal verb just surfaces the daemon's typed refusal. Reversible verbs fire
 * directly and swap in place as the status flips; kill goes behind a confirm.
 */
export function LifecycleActions({
  state,
  onKilled,
}: {
  state: WorkspaceStateView;
  onKilled: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const verbs = useWorkspaceActions(state.id);
  const actions = availableActions(state);
  const pending = verbs.pause.isPending || verbs.resume.isPending || verbs.respawn.isPending;
  const failure = verbs.pause.error ?? verbs.resume.error ?? verbs.respawn.error ?? verbs.kill.error;

  const run: Record<LifecycleAction, () => void> = {
    pause: () => verbs.pause.mutate(),
    resume: () => verbs.resume.mutate(),
    respawn: () => verbs.respawn.mutate(),
    kill: () => setConfirming(true),
  };

  return (
    <div className="flex flex-col gap-1.5" data-testid="lifecycle-actions">
      <div className="flex flex-wrap items-center gap-1.5">
        {actions.map((action) => (
          <Button
            key={action}
            size="sm"
            variant={action === "kill" ? "destructive" : "outline"}
            disabled={pending}
            onClick={run[action]}
            data-testid={`lifecycle-${action}`}
          >
            {ICONS[action]}
            {LABELS[action]}
          </Button>
        ))}
      </div>
      {failure && (
        <p role="status" className="text-xs">
          {failure.message}
        </p>
      )}
      <KillDialog
        state={state}
        open={confirming}
        onOpenChange={setConfirming}
        pending={verbs.kill.isPending}
        // The peek 404s the moment the worktree is gone, so leaving the page is
        // part of the verb rather than something the re-fetch can discover.
        onConfirm={(deleteBranch) => verbs.kill.mutate({ deleteBranch }, { onSuccess: onKilled })}
      />
    </div>
  );
}

const LABELS: Record<LifecycleAction, string> = {
  pause: "Pause",
  resume: "Resume",
  respawn: "Respawn",
  kill: "Kill",
};

const ICONS: Record<LifecycleAction, React.ReactNode> = {
  pause: <PauseIcon aria-hidden />,
  resume: <PlayIcon aria-hidden />,
  respawn: <RotateCwIcon aria-hidden />,
  kill: <Trash2Icon aria-hidden />,
};
