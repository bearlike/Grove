"use client";

import { use } from "react";
import { useRouter } from "next/navigation";
import { Header } from "@/components/layout/header";
import { Skeleton } from "@/components/ui/skeleton";
import { ContextBar } from "@/components/workspace/context-bar";
import { AgentWorkspace } from "@/components/workspace/agent-workspace";
import { AgentLiveStatus } from "@/lib/grove/agent-activity";
import {
  useWorkspaceCommits,
  useWorkspacePeek,
  useWorkspaceSessions,
} from "@/lib/grove/hooks";

type AgentTab = "transcript" | "terminal";

/**
 * The workspace detail page is a contained app-shell, not a scrolling document:
 * under the app header sits a compact ContextBar (identity + state + lifecycle
 * actions + the branch summary popover) and, filling the rest of the viewport,
 * the AgentWorkspace (transcript-first tabs, with a lg+ split). On lg+ the page
 * is a fixed-height flex column (`100dvh` minus the h-14 header and the h-7
 * status bar) whose panes scroll internally; on mobile it falls back to natural
 * document flow and each pane carries its own min-height floor.
 */
export default function DetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const { data, isLoading, isError, error } = useWorkspacePeek(id);
  const { data: commits, isLoading: commitsLoading } = useWorkspaceCommits(id);
  const { data: sessions, isLoading: sessionsLoading } = useWorkspaceSessions(id);

  // Head session is the one being steered (newest-first on the wire) — same
  // convention as the dashboard's displayState.
  const live = AgentLiveStatus.of(sessions?.[0]?.activity ?? null);

  // Default tab is derived, never stored: Transcript once a session resolves,
  // Terminal otherwise, null (skeleton) while loading — so the wrong tab never
  // flashes and an explicit click still wins (AgentWorkspace owns that state).
  const defaultTab: AgentTab | null = sessionsLoading
    ? null
    : sessions && sessions.length > 0
      ? "transcript"
      : "terminal";

  return (
    <>
      <Header />
      <main className="flex flex-col lg:h-[calc(100dvh-5.25rem)] lg:min-h-0">
        {isLoading && <DetailSkeleton />}
        {isError && (
          <div
            role="alert"
            className="m-4 rounded-md border border-[var(--status-error)] bg-[var(--status-error)]/10 p-4 text-sm text-foreground"
          >
            Could not load workspace: {String((error as Error).message)}
          </div>
        )}

        {data && (
          <>
            <ContextBar
              peek={data}
              live={live}
              commits={commits}
              commitsLoading={commitsLoading}
              onKilled={() => router.push("/")}
            />
            <AgentWorkspace
              workspaceId={id}
              snapshot={data.agent_snapshot}
              takenAt={data.snapshot_taken_at}
              tmuxSession={data.state.tmux_session}
              defaultTab={defaultTab}
              subagents={live.subagents}
            />
          </>
        )}
      </main>
    </>
  );
}

function DetailSkeleton() {
  return (
    <div className="flex flex-1 flex-col gap-3 p-4">
      <Skeleton className="h-16 w-full" />
      <Skeleton className="min-h-[24rem] flex-1" />
    </div>
  );
}
