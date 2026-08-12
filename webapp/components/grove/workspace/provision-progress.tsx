"use client";

import { TerminalBlock } from "@/components/elements/terminal-block";
import { useProvisionProgress } from "@/lib/grove/hooks";

/**
 * The container build, while it is happening.
 *
 * Without this the workspace reads OFFLINE for the entire build window — which
 * can be minutes on a cold image — and the only honest remedy a user could
 * infer would be to respawn a workspace that is working perfectly well.
 */
export function ProvisionProgress({ id }: { id: string }) {
  const { data } = useProvisionProgress(id, true);
  const lines = data?.lines ?? [];

  return (
    <div className="flex flex-col gap-3 p-4" data-testid="provision-progress">
      <div className="flex items-baseline gap-2">
        <p className="text-sm font-medium">{data?.headline ?? "Provisioning the container"}</p>
        {data?.elapsed_ms != null && (
          <span className="text-xs tabular-nums">{Math.round(data.elapsed_ms / 1000)}s</span>
        )}
      </div>
      <TerminalBlock
        command="devcontainer up"
        lines={lines}
        visibleCount={lines.length}
        done={false}
        className="max-w-none"
      />
    </div>
  );
}
